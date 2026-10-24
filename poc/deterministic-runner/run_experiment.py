from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiment import build_replay_stages, compare_workflow_reviews, find_previous_review
from runner import PipelineFailed, PipelineRunner, SubprocessExecutor


POC_ROOT = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = POC_ROOT.parents[1]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay deterministic October job-agent stages with hard timeouts and parity checks."
    )
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--source-output-dir", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--python", dest="python_executable", type=Path)
    parser.add_argument("--stage-timeout-seconds", type=int, default=120)
    parser.add_argument("--test-timeout-seconds", type=int, default=180)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    source_output_dir = (args.source_output_dir or repo_root / "outputs").resolve()
    work_dir = args.work_dir.resolve()
    python_executable = (
        args.python_executable or repo_root / ".venv" / "bin" / "python"
    ).resolve()
    try:
        inputs = prepare_replay_inputs(
            repo_root=repo_root,
            source_output_dir=source_output_dir,
            work_dir=work_dir,
            run_date=args.run_date,
            python_executable=python_executable,
        )
        manifest_path = work_dir / "experiment-a-manifest.json"
        stages = build_replay_stages(
            repo_root=repo_root,
            work_dir=work_dir,
            source_output_dir=source_output_dir,
            run_date=args.run_date,
            python_executable=python_executable,
            stage_timeout_seconds=args.stage_timeout_seconds,
            test_timeout_seconds=args.test_timeout_seconds,
        )
        run_id = f"experiment-a-{args.run_date}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
        result = PipelineRunner(
            executor=SubprocessExecutor(),
            cwd=repo_root,
            manifest_path=manifest_path,
        ).run(
            stages,
            run_id=run_id,
            metadata={
                "experiment": "A",
                "mode": "isolated_replay",
                "run_date": args.run_date,
                "inputs": inputs,
                "model_calls_inside_runner": 0,
                "non_exercised": [
                    "live ATS discovery",
                    "jobtracker applied-status refresh",
                    "networking pass",
                    "resume and cover-letter regeneration",
                    "analytics publication",
                    "AI Apply, BlitzAPI, and browser-board passes",
                ],
            },
            resume=args.resume,
        )
        parity = compare_workflow_reviews(
            source_output_dir / f"{args.run_date}-workflow-review.json",
            work_dir / "outputs" / f"{args.run_date}-workflow-review.json",
        )
        final_result = {
            "schema_version": 1,
            "status": result.status,
            "manifest": str(manifest_path),
            "stage_count": len(result.stages),
            "executed_stage_count": sum(stage.status == "passed" for stage in result.stages),
            "reused_stage_count": sum(stage.status == "reused" for stage in result.stages),
            "duration_ms": result.duration_ms,
            "model_calls_inside_runner": 0,
            "parity": parity.to_json_dict(),
            "non_exercised": result.metadata["non_exercised"],
        }
        result_path = work_dir / "experiment-a-result.json"
        result_path.write_text(json.dumps(final_result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(final_result, sort_keys=True))
        return 0
    except (FileNotFoundError, ValueError, PipelineFailed, RuntimeError) as error:
        print(f"Experiment A failed: {error}", file=sys.stderr)
        return 1


def prepare_replay_inputs(
    *,
    repo_root: Path,
    source_output_dir: Path,
    work_dir: Path,
    run_date: str,
    python_executable: Path,
) -> Mapping[str, Any]:
    date.fromisoformat(run_date)
    if not repo_root.is_dir():
        raise FileNotFoundError(f"Repository root does not exist: {repo_root}")
    if not source_output_dir.is_dir():
        raise FileNotFoundError(f"Source output directory does not exist: {source_output_dir}")
    if not python_executable.is_file():
        raise FileNotFoundError(f"Python executable does not exist: {python_executable}")
    baseline_files = (
        source_output_dir / f"{run_date}-discovered-jobs.json",
        source_output_dir / f"{run_date}-applied-jobs.json",
        source_output_dir / f"{run_date}-workflow-review.json",
        source_output_dir / f"{run_date}-networking-matches.md",
    )
    for path in baseline_files:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Required replay input is missing or empty: {path}")
    previous_review = find_previous_review(source_output_dir, run_date)
    output_dir = work_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    copied_previous = output_dir / previous_review.name
    if copied_previous.resolve() != previous_review.resolve():
        shutil.copy2(previous_review, copied_previous)
    networking_source = source_output_dir / f"{run_date}-networking-matches.md"
    copied_networking = output_dir / networking_source.name
    if copied_networking.resolve() != networking_source.resolve():
        shutil.copy2(networking_source, copied_networking)
    inputs = tuple((*baseline_files, previous_review))
    return {
        "source_output_dir": str(source_output_dir),
        "files": [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in inputs
        ],
        "seeded_previous_review": str(copied_previous),
        "seeded_networking": str(copied_networking),
    }


if __name__ == "__main__":
    raise SystemExit(main())
