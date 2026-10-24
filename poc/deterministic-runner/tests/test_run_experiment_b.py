from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Sequence
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from run_experiment_b import (  # noqa: E402
    CodexExecution,
    SubprocessCodexExecutor,
    build_codex_command,
    main,
    run_experiment_b,
)


@dataclass
class FakeExecutor:
    events: str
    verdict: dict[str, object]
    exit_code: int = 0
    stderr: str = ""
    argv: tuple[str, ...] = ()
    stdin: str = ""

    def execute(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        stdin: str,
        timeout_seconds: int,
    ) -> CodexExecution:
        self.argv = tuple(argv)
        self.stdin = stdin
        verdict_path = Path(self.argv[self.argv.index("--output-last-message") + 1])
        verdict_path.write_text(json.dumps(self.verdict))
        return CodexExecution(self.exit_code, self.events, self.stderr)


def valid_verdict() -> dict[str, object]:
    return {
        "status": "verified_with_known_gaps",
        "checks": [
            {"name": "stages", "status": "passed", "evidence": "14/14 passed"}
        ],
        "exceptions": [
            {"severity": "medium", "stage": "discovery", "detail": "source errors recorded"}
        ],
        "summary": "The compact evidence is internally consistent.",
        "recommended_action": "Keep human approval for consequential actions.",
    }


def create_live_fixture(root: Path) -> Path:
    output = root / "outputs"
    output.mkdir(parents=True)
    (root / "external-source-status.json").write_text(
        json.dumps(
            {
                "analytics": {"status": "enabled", "mode": "export_only"},
                "application_submission": {"status": "prohibited", "mode": "no_mutation"},
            }
        )
    )
    (output / "2026-10-08-discovered-jobs.json").write_text(
        json.dumps({"jobs": [], "errors": []})
    )
    (root / "experiment-a-live-manifest.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "duration_ms": 50,
                "metadata": {"model_calls_inside_runner": 0},
                "stages": [
                    {
                        "name": "targeted_tests",
                        "status": "passed",
                        "duration_ms": 10,
                        "exit_code": 0,
                        "error": "",
                        "artifacts": [],
                    }
                ],
            }
        )
    )
    (root / "experiment-a-live-result.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "stage_count": 1,
                "executed_stage_count": 1,
                "analytics_mode": "export_only",
                "model_calls_inside_runner": 0,
                "metrics": {"discovered_jobs": 0, "discovery_errors": 0},
                "baseline_parity": {"status": "exact"},
            }
        )
    )
    return root


class ExperimentBCommandTests(unittest.TestCase):
    def test_builds_ephemeral_read_only_command(self) -> None:
        command = build_codex_command(
            codex_executable=Path("/usr/local/bin/codex"),
            model="gpt-6-luna",
            reasoning_effort="low",
            schema_path=Path("/tmp/schema.json"),
            verdict_path=Path("/tmp/verdict.json"),
        )
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertEqual("read-only", command[command.index("--sandbox") + 1])
        self.assertIn('approval_policy="never"', command)
        self.assertIn("--json", command)
        self.assertEqual("-", command[-1])

    def test_subprocess_executor_captures_output(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="events", stderr="warning")
        with patch("run_experiment_b.subprocess.run", return_value=completed) as run:
            result = SubprocessCodexExecutor().execute(
                ("codex", "exec"),
                cwd=Path("/tmp"),
                stdin="prompt",
                timeout_seconds=20,
            )
        self.assertEqual(CodexExecution(0, "events", "warning"), result)
        self.assertEqual("prompt", run.call_args.kwargs["input"])

    def test_subprocess_executor_converts_timeout_to_explicit_failure(self) -> None:
        with patch(
            "run_experiment_b.subprocess.run",
            side_effect=subprocess.TimeoutExpired(("codex",), 20),
        ):
            with self.assertRaisesRegex(RuntimeError, "exceeded 20"):
                SubprocessCodexExecutor().execute(
                    ("codex", "exec"),
                    cwd=Path("/tmp"),
                    stdin="prompt",
                    timeout_seconds=20,
                )


class ExperimentBRunTests(unittest.TestCase):
    def test_runs_one_tool_free_verifier_and_records_savings(self) -> None:
        events = "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "fresh"}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 2500,
                            "cached_input_tokens": 0,
                            "output_tokens": 250,
                        },
                    }
                ),
            )
        )
        executor = FakeExecutor(events=events, verdict=valid_verdict())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = create_live_fixture(root / "live")
            output = root / "experiment-b"

            result = run_experiment_b(
                live_work_dir=live,
                output_dir=output,
                run_date="2026-10-08",
                codex_executable=Path("/usr/local/bin/codex"),
                model="gpt-6-luna",
                reasoning_effort="low",
                timeout_seconds=300,
                baseline_input_tokens=16_051_827,
                baseline_output_tokens=25_225,
                baseline_responses=115,
                executor=executor,
            )

            self.assertEqual("passed", result["status"])
            self.assertEqual(1, result["measurement"]["completed_turns"])
            self.assertEqual(0, result["measurement"]["tool_calls"])
            self.assertGreater(result["savings"]["input_token_reduction_percent"], 99.9)
            self.assertTrue((output / "compact-verification-bundle.json").is_file())
            self.assertTrue((output / "verifier-verdict.json").is_file())
            self.assertIn("Do not call tools", executor.stdin)

    def test_rejects_verifier_tool_use(self) -> None:
        events = "\n".join(
            (
                json.dumps({"type": "item.started", "item": {"type": "command_execution"}}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                    }
                ),
            )
        )
        executor = FakeExecutor(events=events, verdict=valid_verdict())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "tool-free"):
                run_experiment_b(
                    live_work_dir=create_live_fixture(root / "live"),
                    output_dir=root / "out",
                    run_date="2026-10-08",
                    codex_executable=Path("/codex"),
                    model="gpt-6-luna",
                    reasoning_effort="low",
                    timeout_seconds=300,
                    baseline_input_tokens=1000,
                    baseline_output_tokens=100,
                    baseline_responses=10,
                    executor=executor,
                )


class ExperimentBMainTests(unittest.TestCase):
    def test_main_uses_discovered_codex_and_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("run_experiment_b.shutil.which", return_value="/usr/local/bin/codex"),
                patch("run_experiment_b.run_experiment_b", return_value={"status": "passed"}) as run,
            ):
                exit_code = main(
                    [
                        "--live-work-dir",
                        directory,
                        "--output-dir",
                        str(Path(directory) / "out"),
                    ]
                )
        self.assertEqual(0, exit_code)
        self.assertEqual(Path("/usr/local/bin/codex"), run.call_args.kwargs["codex_executable"])

    def test_main_fails_when_codex_is_unavailable(self) -> None:
        with patch("run_experiment_b.shutil.which", return_value=None):
            exit_code = main(
                [
                    "--live-work-dir",
                    "/tmp/live",
                    "--output-dir",
                    "/tmp/out",
                ]
            )
        self.assertEqual(1, exit_code)

    def test_main_reports_runtime_failure(self) -> None:
        with patch(
            "run_experiment_b.run_experiment_b",
            side_effect=RuntimeError("deliberate verifier failure"),
        ):
            exit_code = main(
                [
                    "--live-work-dir",
                    "/tmp/live",
                    "--output-dir",
                    "/tmp/out",
                    "--codex-executable",
                    "/usr/local/bin/codex",
                ]
            )
        self.assertEqual(1, exit_code)


if __name__ == "__main__":
    unittest.main()
