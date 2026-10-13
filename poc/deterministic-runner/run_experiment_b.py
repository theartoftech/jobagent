from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from verifier import (
    build_verification_bundle,
    build_verifier_prompt,
    calculate_savings,
    parse_codex_events,
    serialize_compact_bundle,
    validate_verdict,
    verifier_output_schema,
)


BASELINE_INPUT_TOKENS = 16_051_827
BASELINE_OUTPUT_TOKENS = 25_225
BASELINE_RESPONSES = 115


@dataclass(frozen=True)
class CodexExecution:
    exit_code: int
    stdout: str
    stderr: str


class CodexExecutor(Protocol):
    def execute(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        stdin: str,
        timeout_seconds: int,
    ) -> CodexExecution: ...


class SubprocessCodexExecutor:
    def execute(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        stdin: str,
        timeout_seconds: int,
    ) -> CodexExecution:
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                input=stdin,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"Fresh verifier exceeded {timeout_seconds} second timeout"
            ) from error
        return CodexExecution(completed.returncode, completed.stdout, completed.stderr)


def build_codex_command(
    *,
    codex_executable: Path,
    model: str,
    reasoning_effort: str,
    schema_path: Path,
    verdict_path: Path,
) -> tuple[str, ...]:
    if not model.strip() or not reasoning_effort.strip():
        raise ValueError("Model and reasoning effort cannot be empty")
    return (
        str(codex_executable),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--config",
        'approval_policy="never"',
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--model",
        model,
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(verdict_path),
        "--color",
        "never",
        "--json",
        "-",
    )


def run_experiment_b(
    *,
    live_work_dir: Path,
    output_dir: Path,
    run_date: str,
    codex_executable: Path,
    model: str,
    reasoning_effort: str,
    timeout_seconds: int,
    baseline_input_tokens: int,
    baseline_output_tokens: int,
    baseline_responses: int,
    executor: CodexExecutor,
) -> Mapping[str, Any]:
    date.fromisoformat(run_date)
    if timeout_seconds <= 0:
        raise ValueError("Verifier timeout must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle = build_verification_bundle(live_work_dir, run_date)
    serialized_bundle = serialize_compact_bundle(bundle, max_bytes=25_000)
    prompt = build_verifier_prompt(serialized_bundle)

    bundle_path = output_dir / "compact-verification-bundle.json"
    schema_path = output_dir / "verifier-output-schema.json"
    prompt_path = output_dir / "verifier-prompt.txt"
    verdict_path = output_dir / "verifier-verdict.json"
    events_path = output_dir / "verifier-events.jsonl"
    stderr_path = output_dir / "verifier-stderr.log"
    bundle_path.write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    schema_path.write_text(
        json.dumps(verifier_output_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    prompt_path.write_text(prompt + "\n", encoding="utf-8")

    command = build_codex_command(
        codex_executable=codex_executable,
        model=model,
        reasoning_effort=reasoning_effort,
        schema_path=schema_path,
        verdict_path=verdict_path,
    )
    execution = executor.execute(
        command,
        cwd=output_dir,
        stdin=prompt,
        timeout_seconds=timeout_seconds,
    )
    events_path.write_text(execution.stdout, encoding="utf-8")
    stderr_path.write_text(execution.stderr, encoding="utf-8")
    if execution.exit_code != 0:
        raise RuntimeError(
            f"Fresh verifier exited with status {execution.exit_code}; see {stderr_path}"
        )
    if not verdict_path.is_file() or verdict_path.stat().st_size == 0:
        raise RuntimeError("Fresh verifier did not write a verdict")

    measurement = parse_codex_events(execution.stdout)
    if measurement.completed_turns != 1:
        raise RuntimeError(
            f"Fresh verifier must use exactly one completed turn, got {measurement.completed_turns}"
        )
    if measurement.tool_calls != 0:
        raise RuntimeError(
            f"Fresh verifier must remain tool-free, got {measurement.tool_calls} tool calls"
        )
    try:
        verdict_value: object = json.loads(verdict_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Fresh verifier verdict is invalid JSON: {error}") from error
    if not isinstance(verdict_value, dict):
        raise RuntimeError("Fresh verifier verdict must be a JSON object")
    verdict = verdict_value
    validate_verdict(verdict)
    savings = calculate_savings(
        baseline_input_tokens=baseline_input_tokens,
        baseline_output_tokens=baseline_output_tokens,
        baseline_responses=baseline_responses,
        verifier_input_tokens=measurement.input_tokens,
        verifier_output_tokens=measurement.output_tokens,
        verifier_responses=measurement.completed_turns,
    )
    result: Mapping[str, Any] = {
        "schema_version": 1,
        "experiment": "B",
        "status": "passed",
        "run_date": run_date,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "authentication": "existing_chatgpt_login",
        "controller_model_calls": 0,
        "bundle_bytes": len(serialized_bundle.encode("utf-8")),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "measurement": measurement.to_json_dict(),
        "savings": savings,
        "verdict": verdict,
        "artifacts": {
            "bundle": str(bundle_path),
            "schema": str(schema_path),
            "prompt": str(prompt_path),
            "events": str(events_path),
            "stderr": str(stderr_path),
            "verdict": str(verdict_path),
        },
    }
    result_path = output_dir / "experiment-b-result.json"
    result_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one fresh, compact, tool-free hosted verifier for Experiment B."
    )
    parser.add_argument("--live-work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--baseline-input-tokens", type=int, default=BASELINE_INPUT_TOKENS)
    parser.add_argument("--baseline-output-tokens", type=int, default=BASELINE_OUTPUT_TOKENS)
    parser.add_argument("--baseline-responses", type=int, default=BASELINE_RESPONSES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    codex_path = args.codex_executable
    if codex_path is None:
        discovered = shutil.which("codex")
        if discovered is None:
            print("Experiment B failed: codex executable is not available", file=sys.stderr)
            return 1
        codex_path = Path(discovered)
    try:
        result = run_experiment_b(
            live_work_dir=args.live_work_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            run_date=args.run_date,
            codex_executable=codex_path.resolve(),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout_seconds=args.timeout_seconds,
            baseline_input_tokens=args.baseline_input_tokens,
            baseline_output_tokens=args.baseline_output_tokens,
            baseline_responses=args.baseline_responses,
            executor=SubprocessCodexExecutor(),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        print(f"Experiment B failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
