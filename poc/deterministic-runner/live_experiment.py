from __future__ import annotations

import json
import hashlib
import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, cast

from runner import StageSpec


def promote_validated_outputs(
    *,
    repo_root: Path,
    work_dir: Path,
    run_date: str,
) -> tuple[str, ...]:
    """Atomically promote one successful staged run into the repository outputs."""
    staging = work_dir / "outputs"
    destination = repo_root / "outputs"
    manifests = (
        staging / f"{run_date}-resume-production.json",
        staging / f"{run_date}-cover-letter-production.json",
    )
    rewritten_manifests: dict[Path, bytes] = {}
    document_copies: list[tuple[Path, Path]] = []
    for manifest_path in manifests:
        manifest = _load_json_object(manifest_path)
        records = manifest.get("records")
        if not isinstance(records, list):
            raise ValueError(f"Production manifest requires records: {manifest_path}")
        category = "cover_letters" if "cover-letter" in manifest_path.name else "resumes"
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"Production manifest record must be an object: {manifest_path}")
            if record.get("validation_status") != "validated":
                raise ValueError(f"Production promotion requires validated records: {manifest_path}")
            stored_path = record.get("artifact_path")
            digest = record.get("artifact_sha256")
            if not isinstance(stored_path, str) or not stored_path.strip():
                raise ValueError(f"Validated production record has no artifact_path: {manifest_path}")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError(f"Validated production record has no artifact_sha256: {manifest_path}")
            source = Path(stored_path)
            if not source.is_absolute():
                source = repo_root / source
                if not source.is_file():
                    source = work_dir / stored_path
            if not source.is_file() or source.stat().st_size == 0:
                raise FileNotFoundError(f"Missing validated document referenced by {manifest_path}: {source}")
            actual_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if actual_digest != digest:
                raise ValueError(f"Validated document hash mismatch: {source}")
            target = destination / category / source.name
            document_copies.append((source, target))
            record["artifact_path"] = str(Path("outputs") / category / source.name)
        rewritten_manifests[manifest_path] = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    dated_files = tuple(
        sorted(
            path
            for entry in staging.glob(f"{run_date}-*")
            for path in (entry.rglob("*") if entry.is_dir() else (entry,))
            if path.is_file() and path not in manifests
        )
    )
    state_files = tuple(
        path
        for path in (work_dir / "state").glob("*.json")
        if path.is_file() and path.stat().st_size > 0
    )
    destination.mkdir(parents=True, exist_ok=True)
    promoted: list[str] = []
    for source, target in (*document_copies, *((path, destination / path.relative_to(staging)) for path in dated_files), *((path, destination / path.name) for path in state_files)):
        _atomic_copy(source, target)
        promoted.append(str(target))
    for source, content in rewritten_manifests.items():
        target = destination / source.name
        _atomic_write(target, content)
        promoted.append(str(target))
    return tuple(promoted)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def _atomic_write(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    temporary.replace(destination)


def build_live_stages(
    *,
    repo_root: Path,
    work_dir: Path,
    run_date: str,
    python_wrapper: Path,
    discovery_timeout_seconds: int,
    stage_timeout_seconds: int,
    document_timeout_seconds: int,
    test_timeout_seconds: int,
    publish_analytics: bool,
    analytics_ssh_host: str | None,
) -> tuple[StageSpec, ...]:
    timeouts = (
        discovery_timeout_seconds,
        stage_timeout_seconds,
        document_timeout_seconds,
        test_timeout_seconds,
    )
    if any(value <= 0 for value in timeouts):
        raise ValueError("Live stage timeouts must be positive")
    if publish_analytics and not (analytics_ssh_host or "").strip():
        raise ValueError("analytics_ssh_host is required when analytics publication is enabled")

    output = work_dir / "outputs"
    state = work_dir / "state"
    discovered = output / f"{run_date}-discovered-jobs.json"
    daily = output / f"{run_date}-daily-job-search.md"
    inspection = output / f"{run_date}-job-inspection-queue.md"
    applied = output / f"{run_date}-applied-jobs.json"
    review = output / f"{run_date}-workflow-review.json"
    ready = output / f"{run_date}-resume-ready-jobs.md"
    networking = output / f"{run_date}-networking-matches.md"
    daily_html = output / f"{run_date}-daily-job-search.html"
    workflow_html = output / f"{run_date}-workflow-review.html"
    resume_manifest = output / f"{run_date}-resume-production.json"
    cover_manifest = output / f"{run_date}-cover-letter-production.json"
    analytics_artifact = output / f"{run_date}-skill-gap-observations.json"
    analytics_result = output / f"{run_date}-skill-gap-import-result.json"

    def command(script: str, *arguments: str) -> tuple[str, ...]:
        return (str(python_wrapper), str(repo_root / "scripts" / script), *arguments)

    analytics_arguments = [
        "--workflow-review",
        str(review),
        "--workflow-profile",
        str(repo_root / "candidate_workflow_profile.yml"),
        "--out",
        str(output),
        "--date",
        run_date,
    ]
    analytics_outputs = (analytics_artifact,)
    if publish_analytics:
        analytics_arguments.extend(("--ssh-host", cast(str, analytics_ssh_host)))
        analytics_outputs = (analytics_artifact, analytics_result)
    else:
        analytics_arguments.append("--export-only")

    return (
        StageSpec(
            "live_discovery",
            command(
                "discover_jobs_sharded.py",
                "--companies",
                str(repo_root / "companies.yml"),
                "--profile",
                str(repo_root / "candidate_profile.yml"),
                "--out",
                str(output),
                "--max-age-days",
                "60",
                "--ats-registry",
                str(repo_root / "ats_registry.yml"),
                "--manual-job-urls",
                str(repo_root / "manual_job_urls.yml"),
                "--job-overrides",
                str(repo_root / "job_overrides.yml"),
                "--startup-watchlist",
                str(repo_root / "startup_watchlist.yml"),
                "--rotation",
                "--pause-seconds",
                "0",
                "--shard-count",
                "10",
                "--max-companies-per-shard",
                "20",
                "--per-shard-timeout-seconds",
                "900",
                "--sentinel-state",
                str(state / "ats-sentinel-state.json"),
                "--deep-scan-state",
                str(state / "company-deep-scan-state.json"),
            ),
            discovery_timeout_seconds,
            (
                discovered,
                output / f"{run_date}-shard-summary.md",
                output / f"{run_date}-shard-manifest.json",
                output / f"{run_date}-ats-sentinel.json",
            ),
        ),
        StageSpec(
            "search_plan",
            command(
                "search_jobs.py",
                "--companies",
                str(repo_root / "companies.yml"),
                "--profile",
                str(repo_root / "candidate_profile.yml"),
                "--rules",
                str(repo_root / "search_rules.md"),
                "--job-overrides",
                str(repo_root / "job_overrides.yml"),
                "--out",
                str(output),
            ),
            stage_timeout_seconds,
            (output / f"{run_date}-search-plan.md", daily),
        ),
        StageSpec(
            "inspection_queue",
            command(
                "build_job_inspection_queue.py",
                "--discovered-jobs",
                str(discovered),
                "--out",
                str(output),
                "--date",
                run_date,
                "--job-overrides",
                str(repo_root / "job_overrides.yml"),
            ),
            stage_timeout_seconds,
            (inspection,),
        ),
        StageSpec(
            "daily_report_initial",
            command(
                "build_job_inspection_queue.py",
                "--discovered-jobs",
                str(discovered),
                "--out",
                str(output),
                "--date",
                run_date,
                "--job-overrides",
                str(repo_root / "job_overrides.yml"),
                "--daily-report",
                str(daily),
            ),
            stage_timeout_seconds,
            (daily,),
        ),
        StageSpec(
            "tracker_before_review",
            command(
                "mark_applied_jobs.py",
                "--daily-report",
                str(daily),
                "--applied-jobs-out",
                str(applied),
            ),
            stage_timeout_seconds,
            (daily, applied),
        ),
        StageSpec(
            "strict_review",
            command(
                "review_job_fit.py",
                "--discovered-jobs",
                str(discovered),
                "--workflow-profile",
                str(repo_root / "candidate_workflow_profile.yml"),
                "--job-overrides",
                str(repo_root / "job_overrides.yml"),
                "--applied-jobs",
                str(applied),
                "--out",
                str(output),
                "--date",
                run_date,
            ),
            stage_timeout_seconds,
            (review, ready, workflow_html),
        ),
        StageSpec(
            "daily_report_sync",
            command(
                "build_job_inspection_queue.py",
                "--discovered-jobs",
                str(discovered),
                "--out",
                str(output),
                "--date",
                run_date,
                "--job-overrides",
                str(repo_root / "job_overrides.yml"),
                "--daily-report",
                str(daily),
                "--workflow-review",
                str(review),
            ),
            stage_timeout_seconds,
            (daily, inspection),
        ),
        StageSpec(
            "tracker_after_sync",
            command(
                "mark_applied_jobs.py",
                "--daily-report",
                str(daily),
                "--applied-jobs-out",
                str(applied),
            ),
            stage_timeout_seconds,
            (daily, applied),
        ),
        StageSpec(
            "networking",
            command(
                "match_network.py",
                "--daily-report",
                str(daily),
                "--out",
                str(output),
                "--date",
                run_date,
            ),
            stage_timeout_seconds,
            (networking, daily),
        ),
        StageSpec(
            "daily_html",
            command(
                "render_daily_job_search_html.py",
                "--report",
                str(daily),
                "--networking",
                str(networking),
                "--out",
                str(daily_html),
            ),
            stage_timeout_seconds,
            (daily_html,),
        ),
        StageSpec(
            "documents",
            command(
                "produce_resumes.py",
                "--resume-ready",
                str(ready),
                "--applied-jobs",
                str(applied),
                "--source-dir",
                str(repo_root / "Source Docs"),
                "--out",
                str(output / "resumes"),
                "--template-config",
                str(repo_root / "resume_templates.json"),
            ),
            document_timeout_seconds,
            (resume_manifest, cover_manifest, workflow_html),
            (("JOBAGENT_RENDERER_MODE", "docker"),),
        ),
        StageSpec(
            "analytics",
            command("publish_skill_gap_analytics.py", *analytics_arguments),
            stage_timeout_seconds,
            analytics_outputs,
        ),
        StageSpec(
            "workflow_html",
            command(
                "render_workflow_review_html.py",
                "--workflow-review",
                str(review),
                "--out",
                str(workflow_html),
            ),
            stage_timeout_seconds,
            (workflow_html,),
        ),
        StageSpec(
            "targeted_tests",
            (
                str(python_wrapper),
                "-m",
                "unittest",
                "tests/test_discover_jobs_sharded.py",
        "tests/test_platform_requirements.py",
        "tests/test_posting_availability.py",
        "tests/test_qualification_requirements.py",
                "tests/test_scoring_improvements.py",
                "tests/test_search_jobs.py",
                "tests/test_build_job_inspection_queue.py",
                "tests/test_mark_applied_jobs.py",
                "tests/test_review_job_fit.py",
                "tests/test_match_network_html.py",
                "tests/test_produce_resumes.py",
                "tests/test_produce_cover_letters.py",
                "tests/test_publish_skill_gap_analytics.py",
                "tests/test_render_daily_job_search_html.py",
                "tests/test_render_workflow_review_html.py",
                "poc/deterministic-runner/tests/test_live_experiment.py",
            ),
            test_timeout_seconds,
        ),
    )


def seed_validated_documents(
    repo_root: Path,
    work_dir: Path,
    manifest_path: Path,
) -> int:
    value = _load_json_object(manifest_path)
    records = value.get("records")
    if not isinstance(records, list):
        raise ValueError(f"Production manifest requires records: {manifest_path}")
    seeded = 0
    for record in records:
        if not isinstance(record, dict) or record.get("validation_status") != "validated":
            continue
        stored_path = record.get("artifact_path")
        if not isinstance(stored_path, str) or not stored_path.strip():
            raise ValueError(f"Validated production record has no artifact_path: {manifest_path}")
        relative = Path(stored_path)
        source = relative if relative.is_absolute() else repo_root / relative
        if not source.is_file() or source.stat().st_size == 0:
            raise FileNotFoundError(f"Missing validated document referenced by {manifest_path}: {source}")
        if relative.is_absolute():
            category = "cover_letters" if "cover-letter" in manifest_path.name else "resumes"
            destination = work_dir / "outputs" / category / source.name
        else:
            destination = work_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        seeded += 1
    return seeded


def collect_live_metrics(output_dir: Path, run_date: str) -> Mapping[str, Any]:
    discovery = _load_json_object(output_dir / f"{run_date}-discovered-jobs.json")
    review = _load_json_object(output_dir / f"{run_date}-workflow-review.json")
    resumes = _load_json_object(output_dir / f"{run_date}-resume-production.json")
    letters = _load_json_object(output_dir / f"{run_date}-cover-letter-production.json")
    jobs = _required_list(discovery, "jobs")
    errors = _required_list(discovery, "errors")
    reviews = _required_list(review, "reviews")
    resume_records = _required_list(resumes, "records")
    letter_records = _required_list(letters, "records")
    decisions = Counter(
        str(item.get("workflow_decision", ""))
        for item in reviews
        if isinstance(item, dict)
    )
    return {
        "discovered_jobs": len(jobs),
        "discovery_errors": len(errors),
        "reviewed_jobs": len(reviews),
        "decision_counts": dict(sorted(decisions.items())),
        "resume_ready_jobs": sum(
            bool(item.get("should_generate_resume")) and not bool(item.get("already_applied"))
            for item in reviews
            if isinstance(item, dict)
        ),
        "validated_resumes": sum(
            isinstance(item, dict) and item.get("validation_status") == "validated"
            for item in resume_records
        ),
        "validated_cover_letters": sum(
            isinstance(item, dict) and item.get("validation_status") == "validated"
            for item in letter_records
        ),
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read JSON object {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return cast(dict[str, Any], value)


def _required_list(value: Mapping[str, Any], key: str) -> list[Any]:
    result = value.get(key)
    if not isinstance(result, list):
        raise ValueError(f"Expected list field {key!r}")
    return result
