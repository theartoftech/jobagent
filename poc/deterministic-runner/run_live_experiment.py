from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiment import ParityError, compare_workflow_reviews, find_previous_review
from live_experiment import (
    build_live_stages,
    collect_live_metrics,
    promote_validated_outputs,
    seed_validated_documents,
)
from runner import PipelineFailed, PipelineRunner, SubprocessExecutor


POC_ROOT = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = POC_ROOT.parents[1]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the live deterministic job-agent core in an isolated workspace."
    )
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--discovery-timeout-seconds", type=int, default=1_200)
    parser.add_argument("--stage-timeout-seconds", type=int, default=180)
    parser.add_argument("--document-timeout-seconds", type=int, default=900)
    parser.add_argument("--test-timeout-seconds", type=int, default=600)
    parser.add_argument("--publish-analytics", action="store_true")
    parser.add_argument("--analytics-ssh-host")
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--external-source-status-input", type=Path)
    return parser.parse_args(argv)


def prepare_live_inputs(
    *,
    repo_root: Path,
    work_dir: Path,
    run_date: str,
    local_date: date,
    external_source_status_input: Path | None = None,
) -> Mapping[str, Any]:
    parsed_run_date = date.fromisoformat(run_date)
    if parsed_run_date != local_date:
        raise ValueError(
            f"Live experiment run date must equal the current local date {local_date.isoformat()}"
        )
    if not repo_root.is_dir():
        raise FileNotFoundError(f"Repository root does not exist: {repo_root}")
    source_output = (repo_root / "outputs").resolve()
    if not source_output.is_dir():
        raise FileNotFoundError(f"Source output directory does not exist: {source_output}")
    if work_dir.resolve() == source_output:
        raise ValueError("Live experiment work directory must be isolated from source outputs")
    wrapper = repo_root / "scripts" / "jobagent_python.sh"
    if not wrapper.is_file():
        raise FileNotFoundError(f"Job-agent Python wrapper does not exist: {wrapper}")

    output = work_dir / "outputs"
    state = work_dir / "state"
    output.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)

    seeded_files: list[Path] = []
    for state_name in ("ats-sentinel-state.json", "company-deep-scan-state.json"):
        source = source_output / state_name
        if not source.is_file() or source.stat().st_size == 0:
            raise FileNotFoundError(f"Required discovery state is missing or empty: {source}")
        destination = state / state_name
        shutil.copy2(source, destination)
        seeded_files.append(destination)

    previous_review = find_previous_review(source_output, run_date)
    current_review = source_output / f"{run_date}-workflow-review.json"
    baseline_review: Path | None = (
        current_review if current_review.is_file() and current_review.stat().st_size > 0 else None
    )
    previous_destination = output / previous_review.name
    shutil.copy2(previous_review, previous_destination)
    seeded_files.append(previous_destination)

    seeded_document_count = 0
    manifest_status: dict[str, str] = {}
    seeded_manifests: dict[str, str | None] = {}
    for kind in ("resume", "cover-letter"):
        manifest = _latest_production_manifest(source_output, run_date, kind)
        if manifest is None:
            manifest_status[kind] = "not_available"
            seeded_manifests[kind] = None
            continue
        seeded_document_count += seed_validated_documents(repo_root, work_dir, manifest)
        destination = output / manifest.name
        shutil.copy2(manifest, destination)
        seeded_files.append(destination)
        manifest_status[kind] = "seeded"
        seeded_manifests[kind] = manifest.name

    external_status: Mapping[str, Any] = {
        "live_ats": {
            "status": "enabled",
            "mode": "read_only",
            "detail": "Direct ATS shards",
        },
        "jobtracker": {
            "status": "enabled",
            "mode": "read_only",
            "detail": "Applied-status and networking reconciliation",
        },
        "analytics": {
            "status": "enabled",
            "mode": "export_only",
            "detail": "No JobTracker analytics publication without an explicit flag and host",
        },
        "ai_apply": {
            "status": "not_exercised",
            "mode": "unavailable_to_local_runner",
            "detail": "No supported local adapter is present",
        },
        "blitzapi": {
            "status": "not_exercised",
            "mode": "unavailable_to_local_runner",
            "detail": "No supported local adapter is present",
        },
        "browser_boards": {
            "status": "not_exercised",
            "mode": "requires_browser_automation",
            "detail": "Excluded from this deterministic local-core experiment",
        },
        "application_submission": {
            "status": "prohibited",
            "mode": "no_mutation",
            "detail": "The experiment never queues or submits applications",
        },
    }
    if external_source_status_input is not None:
        external_status = _load_external_source_status(external_source_status_input)
    external_status_path = output / f"{run_date}-external-source-status.json"
    external_status_path.write_text(
        json.dumps(external_status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    seeded_files.append(external_status_path)

    return {
        "source_output_dir": str(source_output),
        "baseline_workflow_review": str(baseline_review) if baseline_review else None,
        "baseline_status": "available" if baseline_review else "not_available",
        "seeded_previous_review": str(previous_destination),
        "seeded_document_count": seeded_document_count,
        "production_manifest_status": manifest_status,
        "seeded_manifests": seeded_manifests,
        "external_source_status": str(external_status_path),
        "seeded_files": [_file_record(path) for path in seeded_files],
    }


def _file_record(path: Path) -> Mapping[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _latest_production_manifest(
    output_dir: Path,
    run_date: str,
    kind: str,
) -> Path | None:
    candidates = [
        path
        for path in output_dir.glob(f"*-{kind}-production.json")
        if path.name[:10] <= run_date and path.name[:10].count("-") == 2
    ]
    return max(candidates, key=lambda path: path.name[:10], default=None)


def _load_external_source_status(path: Path) -> Mapping[str, Any]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read external source status {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"External source status must be a JSON object: {path}")
    submission = value.get("application_submission")
    if not isinstance(submission, dict):
        raise ValueError("External source status requires application_submission safety state")
    if submission.get("status") != "prohibited" or submission.get("mode") != "no_mutation":
        raise ValueError("External source status must prohibit application submission")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    work_dir = args.work_dir.resolve()
    try:
        inputs = prepare_live_inputs(
            repo_root=repo_root,
            work_dir=work_dir,
            run_date=args.run_date,
            local_date=date.today(),
            external_source_status_input=args.external_source_status_input,
        )
        analytics_mode = "publish" if args.publish_analytics else "export_only"
        stages = build_live_stages(
            repo_root=repo_root,
            work_dir=work_dir,
            run_date=args.run_date,
            python_wrapper=repo_root / "scripts" / "jobagent_python.sh",
            discovery_timeout_seconds=args.discovery_timeout_seconds,
            stage_timeout_seconds=args.stage_timeout_seconds,
            document_timeout_seconds=args.document_timeout_seconds,
            test_timeout_seconds=args.test_timeout_seconds,
            publish_analytics=args.publish_analytics,
            analytics_ssh_host=args.analytics_ssh_host,
        )
        manifest_path = work_dir / "experiment-a-live-manifest.json"
        run_id = f"experiment-a-live-{args.run_date}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
        run = PipelineRunner(
            executor=SubprocessExecutor(),
            cwd=repo_root,
            manifest_path=manifest_path,
        ).run(
            stages,
            run_id=run_id,
            metadata={
                "experiment": "A",
                "mode": "isolated_live_deterministic_core",
                "run_date": args.run_date,
                "inputs": inputs,
                "analytics_mode": analytics_mode,
                "model_calls_inside_runner": 0,
            },
        )
        metrics = collect_live_metrics(work_dir / "outputs", args.run_date)
        candidate = work_dir / "outputs" / f"{args.run_date}-workflow-review.json"
        baseline_value = inputs["baseline_workflow_review"]
        if baseline_value is None:
            parity: Mapping[str, Any] = {"status": "not_available"}
        else:
            try:
                parity_metrics = compare_workflow_reviews(Path(str(baseline_value)), candidate)
                parity = {
                    "status": "exact",
                    "metrics": parity_metrics.to_json_dict(),
                }
            except ParityError as error:
                parity = {
                    "status": "different_live_snapshot",
                    "detail": str(error),
                }
        promoted_files = (
            promote_validated_outputs(
                repo_root=repo_root,
                work_dir=work_dir,
                run_date=args.run_date,
            )
            if args.promote
            else ()
        )
        final_result = {
            "schema_version": 1,
            "status": run.status,
            "manifest": str(manifest_path),
            "stage_count": len(run.stages),
            "executed_stage_count": sum(stage.status == "passed" for stage in run.stages),
            "duration_ms": run.duration_ms,
            "model_calls_inside_runner": 0,
            "analytics_mode": analytics_mode,
            "metrics": metrics,
            "baseline_parity": parity,
            "external_source_status": inputs["external_source_status"],
            "promotion": {
                "status": "promoted" if args.promote else "not_requested",
                "file_count": len(promoted_files),
            },
        }
        result_path = work_dir / "experiment-a-live-result.json"
        result_path.write_text(
            json.dumps(final_result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(final_result, sort_keys=True))
        return 0
    except (FileNotFoundError, ValueError, ParityError, PipelineFailed, RuntimeError) as error:
        print(f"Live Experiment A failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
