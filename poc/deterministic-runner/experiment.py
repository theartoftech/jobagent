from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from runner import StageSpec


class ParityError(RuntimeError):
    """Raised when replay output differs from the baseline."""


@dataclass(frozen=True)
class ParityMetrics:
    review_count: int
    decision_counts: Mapping[str, int]
    resume_ready_count: int

    def to_json_dict(self) -> Mapping[str, Any]:
        return asdict(self)


def compare_workflow_reviews(baseline_path: Path, candidate_path: Path) -> ParityMetrics:
    baseline = _load_review_map(baseline_path)
    candidate = _load_review_map(candidate_path)
    missing = sorted(set(baseline) - set(candidate))
    extra = sorted(set(candidate) - set(baseline))
    if missing or extra:
        raise ParityError(
            f"Workflow identities differ; missing={_identity_labels(missing)}, "
            f"extra={_identity_labels(extra)}"
        )
    for identity, expected in baseline.items():
        actual = candidate[identity]
        expected_decision = _required_string(expected, "workflow_decision", baseline_path)
        actual_decision = _required_string(actual, "workflow_decision", candidate_path)
        if expected_decision != actual_decision:
            company = _required_string(expected, "company", baseline_path)
            title = _required_string(expected, "title", baseline_path)
            raise ParityError(
                f"Decision mismatch for {company} / {title}: "
                f"expected {expected_decision}, got {actual_decision}"
            )
        if bool(expected.get("should_generate_resume")) != bool(
            actual.get("should_generate_resume")
        ):
            company = _required_string(expected, "company", baseline_path)
            title = _required_string(expected, "title", baseline_path)
            raise ParityError(f"Resume authorization mismatch for {company} / {title}")

    decision_counts = Counter(
        _required_string(review, "workflow_decision", candidate_path)
        for review in candidate.values()
    )
    return ParityMetrics(
        review_count=len(candidate),
        decision_counts=dict(sorted(decision_counts.items())),
        resume_ready_count=sum(
            bool(review.get("should_generate_resume"))
            and not bool(review.get("already_applied"))
            for review in candidate.values()
        ),
    )


def find_previous_review(output_dir: Path, run_date: str) -> Path:
    parsed_date = dt.date.fromisoformat(run_date)
    candidates: list[tuple[dt.date, Path]] = []
    for path in output_dir.glob("????-??-??-workflow-review.json"):
        date_text = path.name.removesuffix("-workflow-review.json")
        try:
            candidate_date = dt.date.fromisoformat(date_text)
        except ValueError:
            continue
        if candidate_date < parsed_date:
            candidates.append((candidate_date, path))
    if not candidates:
        raise FileNotFoundError(
            f"No previous workflow review exists before {run_date} in {output_dir}"
        )
    return max(candidates, key=lambda value: value[0])[1]


def build_replay_stages(
    *,
    repo_root: Path,
    work_dir: Path,
    source_output_dir: Path,
    run_date: str,
    python_executable: Path,
    stage_timeout_seconds: int,
    test_timeout_seconds: int,
) -> tuple[StageSpec, ...]:
    if stage_timeout_seconds <= 0 or test_timeout_seconds <= 0:
        raise ValueError("Stage and test timeouts must be positive")
    output_dir = work_dir / "outputs"
    discovered = source_output_dir / f"{run_date}-discovered-jobs.json"
    applied = source_output_dir / f"{run_date}-applied-jobs.json"
    daily_report = output_dir / f"{run_date}-daily-job-search.md"
    review = output_dir / f"{run_date}-workflow-review.json"
    inspection = output_dir / f"{run_date}-job-inspection-queue.md"
    search_plan = output_dir / f"{run_date}-search-plan.md"
    resume_ready = output_dir / f"{run_date}-resume-ready-jobs.md"
    analytics = output_dir / f"{run_date}-skill-gap-observations.json"
    daily_html = output_dir / f"{run_date}-daily-job-search.html"
    workflow_html = output_dir / f"{run_date}-workflow-review.html"
    networking = output_dir / f"{run_date}-networking-matches.md"
    python = str(python_executable)

    def script(name: str, *arguments: str) -> tuple[str, ...]:
        return (python, str(repo_root / "scripts" / name), *arguments)

    return (
        StageSpec(
            "search_plan",
            script(
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
                str(output_dir),
            ),
            stage_timeout_seconds,
            (search_plan, daily_report),
        ),
        StageSpec(
            "inspection_queue",
            script(
                "build_job_inspection_queue.py",
                "--discovered-jobs",
                str(discovered),
                "--out",
                str(output_dir),
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
            script(
                "build_job_inspection_queue.py",
                "--discovered-jobs",
                str(discovered),
                "--out",
                str(output_dir),
                "--date",
                run_date,
                "--job-overrides",
                str(repo_root / "job_overrides.yml"),
                "--daily-report",
                str(daily_report),
            ),
            stage_timeout_seconds,
            (daily_report,),
        ),
        StageSpec(
            "strict_review",
            script(
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
                str(output_dir),
                "--date",
                run_date,
            ),
            stage_timeout_seconds,
            (review, resume_ready, workflow_html),
        ),
        StageSpec(
            "daily_report_sync",
            script(
                "build_job_inspection_queue.py",
                "--discovered-jobs",
                str(discovered),
                "--out",
                str(output_dir),
                "--date",
                run_date,
                "--job-overrides",
                str(repo_root / "job_overrides.yml"),
                "--daily-report",
                str(daily_report),
                "--workflow-review",
                str(review),
            ),
            stage_timeout_seconds,
            (daily_report, inspection),
        ),
        StageSpec(
            "analytics_export",
            script(
                "publish_skill_gap_analytics.py",
                "--workflow-review",
                str(review),
                "--workflow-profile",
                str(repo_root / "candidate_workflow_profile.yml"),
                "--out",
                str(output_dir),
                "--date",
                run_date,
                "--export-only",
            ),
            stage_timeout_seconds,
            (analytics,),
        ),
        StageSpec(
            "daily_html",
            script(
                "render_daily_job_search_html.py",
                "--report",
                str(daily_report),
                "--networking",
                str(networking),
                "--out",
                str(daily_html),
            ),
            stage_timeout_seconds,
            (daily_html,),
        ),
        StageSpec(
            "workflow_html",
            script(
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
                python,
                "-m",
                "unittest",
                "tests/test_build_job_inspection_queue.py",
                "tests/test_review_job_fit.py",
                "tests/test_render_daily_job_search_html.py",
                "tests/test_render_workflow_review_html.py",
                "tests/test_publish_skill_gap_analytics.py",
            ),
            test_timeout_seconds,
        ),
    )


def _load_review_map(path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ParityError(f"Cannot read workflow review {path}: {error}") from error
    if not isinstance(value, dict):
        raise ParityError(f"Workflow review must be a JSON object: {path}")
    reviews = value.get("reviews")
    if not isinstance(reviews, list):
        raise ParityError(f"Workflow review must contain a reviews array: {path}")
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw_review in reviews:
        if not isinstance(raw_review, dict):
            raise ParityError(f"Workflow review contains a non-object entry: {path}")
        review = cast(dict[str, Any], raw_review)
        company = _required_string(review, "company", path)
        title = _required_string(review, "title", path)
        url = review.get("url")
        normalized_url = url.strip() if isinstance(url, str) else ""
        identity = (normalized_url, company.casefold(), title.casefold())
        if identity in result:
            raise ParityError(f"Duplicate workflow identity for {company} / {title} in {path}")
        result[identity] = review
    return result


def _required_string(value: Mapping[str, Any], key: str, path: Path) -> str:
    field = value.get(key)
    if not isinstance(field, str) or not field.strip():
        raise ParityError(f"Workflow review field {key!r} is missing in {path}")
    return field.strip()


def _identity_labels(identities: Sequence[tuple[str, str, str]]) -> list[str]:
    return [f"{company} / {title}" for _, company, title in identities[:10]]
