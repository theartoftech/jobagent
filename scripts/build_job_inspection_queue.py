#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

try:
    from scripts.discover_jobs import (
        MIN_APPLY_FIT_SCORE,
        NEEDS_FRESHNESS_VERIFICATION_DECISION,
        load_job_overrides,
    )
    from scripts.job_override_dates import override_is_active
except ModuleNotFoundError:
    from discover_jobs import (
        MIN_APPLY_FIT_SCORE,
        NEEDS_FRESHNESS_VERIFICATION_DECISION,
        load_job_overrides,
    )
    from job_override_dates import override_is_active


MAX_APPLY_ROWS_PER_COMPANY = 5
MAX_MAYBE_ROWS_PER_COMPANY = 8
MAX_TOTAL_MAYBE_ROWS = 50
MAX_TOTAL_VERIFICATION_ROWS = 20
MIN_FIT_SCORE_FOR_FRESHNESS_VERIFICATION = 55
DISCOVERY_APPLY_SECTION = "Discovery Candidates — Higher Signal"
DISCOVERY_MAYBE_SECTION = "Discovery Candidates — Review Signal"


@dataclass(frozen=True)
class InspectionJob:
    company: str
    title: str
    job_id: str
    posted_on: str
    posted_age_days: int | None
    location: str
    work_model: str
    fit_score: int
    decision: str
    source: str
    url: str
    risk_flags: tuple[str, ...]


@dataclass(frozen=True)
class JobBuckets:
    apply_jobs: list[InspectionJob]
    maybe_jobs: list[InspectionJob]
    needs_freshness_verification: list[InspectionJob]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def normalize_match_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (value or "").casefold())).strip()


def load_jobs(path: Path) -> list[InspectionJob]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list):
        raise ValueError(f"Expected 'jobs' list in {path}")

    jobs: list[InspectionJob] = []
    for item in raw_jobs:
        if not isinstance(item, dict):
            continue
        jobs.append(
            InspectionJob(
                company=str(item.get("company", "")),
                title=str(item.get("title", "")),
                job_id=str(item.get("job_id", "")),
                posted_on=str(item.get("posted_on", "")),
                posted_age_days=coerce_optional_int(item.get("posted_age_days")),
                location=str(item.get("location", "")),
                work_model=str(item.get("work_model", "")),
                fit_score=coerce_int(item.get("fit_score")),
                decision=str(item.get("decision", "")),
                source=str(item.get("source", "")),
                url=str(item.get("url", "")),
                risk_flags=tuple(str(flag) for flag in item.get("risk_flags", []) if str(flag).strip()),
            )
        )
    return jobs


def load_carry_forward_jobs(out_dir: Path, run_date: str) -> list[InspectionJob]:
    """Carry forward only latest strict-review roles that remain actionable.

    A broad daily report is a diagnostic artifact and cannot authorize its own
    carry-forward. Restricting this to strict workflow state prevents stale,
    rejected, or already-applied listings from reappearing as candidates.
    """
    candidates = [
        path
        for path in out_dir.glob("????-??-??-workflow-review.json")
        if path.name[:10] < run_date
    ]
    if not candidates:
        return []

    latest_path = max(candidates, key=lambda path: path.name[:10])
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected workflow review object in {latest_path}")
    reviews = payload.get("reviews")
    if not isinstance(reviews, list) or any(not isinstance(item, dict) for item in reviews):
        raise ValueError(f"Expected workflow review list in {latest_path}")

    active_identities = {
        job_identity_key(job_from_dict(item))
        for item in reviews
        if isinstance(item, dict) and is_active_strict_review(item)
    }
    if not active_identities:
        return []

    source_jobs = payload.get("source_jobs")
    if source_jobs is None:
        discovered_path = latest_path.with_name(
            latest_path.name.replace("-workflow-review.json", "-discovered-jobs.json")
        )
        raw_jobs = load_jobs(discovered_path) if discovered_path.exists() else []
    else:
        if not isinstance(source_jobs, list) or any(not isinstance(item, dict) for item in source_jobs):
            raise ValueError(f"Expected source jobs list in {latest_path}")
        raw_jobs = [item for item in source_jobs if isinstance(item, dict)]

    return [
        job
        for job in (job_from_dict(item) for item in raw_jobs)
        if job_identity_key(job) in active_identities
    ]


def is_active_strict_review(item: Mapping[str, object]) -> bool:
    if bool(item.get("already_applied", False)):
        return False
    if bool(item.get("should_generate_resume", False)):
        return True
    decision = normalize_text(str(item.get("workflow_decision", "")))
    return bool(item.get("gap_questions_needed", False)) and decision in {
        "strong apply",
        "apply",
        "moderate stretch",
    }


def coerce_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(str(value))


def coerce_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return coerce_int(value)


def job_sort_key(job: InspectionJob) -> tuple[int, int, str, str]:
    return (-job.fit_score, freshness_rank(job.posted_age_days), normalize_text(job.company), normalize_text(job.title))


def freshness_rank(posted_age_days: int | None) -> int:
    if posted_age_days is None:
        return 0
    if posted_age_days <= 1:
        return 0
    if posted_age_days <= 7:
        return 1
    if posted_age_days <= 21:
        return 2
    if posted_age_days <= 60:
        return 3
    return 4


def dedupe_key(job: InspectionJob) -> tuple[str, str]:
    normalized_company = normalize_text(job.company)
    return (normalized_company, normalize_text(job.title))


def canonical_job_url(url: str) -> str:
    """Normalize presentation-only URL differences without changing job identity."""
    parsed = urlsplit((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return normalize_text(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, parsed.query, ""))


def job_identity_key(job: InspectionJob) -> tuple[str, str]:
    normalized_company = normalize_text(job.company)
    canonical_url = canonical_job_url(job.url)
    if canonical_url:
        return (normalized_company, canonical_url)
    if job.job_id:
        return (normalized_company, f"job-id:{normalize_text(job.job_id)}")
    return dedupe_key(job)


def load_strict_review_statuses(path: Path) -> dict[tuple[str, str], tuple[str, bool]]:
    """Load strict decision and applied state keyed by exact official posting identity."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected workflow review object in {path}")
    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError(f"Expected workflow review list in {path}")

    statuses: dict[tuple[str, str], tuple[str, bool]] = {}
    for item in reviews:
        if not isinstance(item, dict):
            raise ValueError(f"Expected workflow review object in {path}")
        review_job = InspectionJob(
            company=str(item.get("company", "")),
            title=str(item.get("title", "")),
            job_id="",
            posted_on="",
            posted_age_days=None,
            location="",
            work_model="",
            fit_score=0,
            decision="",
            source="strict-review",
            url=str(item.get("url", "")),
            risk_flags=(),
        )
        statuses[job_identity_key(review_job)] = (
            str(item.get("workflow_decision", "")).strip().upper(),
            bool(item.get("already_applied", False)),
        )
    return statuses


def filter_buckets_by_strict_review(
    buckets: JobBuckets,
    statuses: Mapping[tuple[str, str], tuple[str, bool]],
) -> JobBuckets:
    """Remove roles that strict review rejected or jobtracker already marks applied."""
    def is_actionable(job: InspectionJob) -> bool:
        status = statuses.get(job_identity_key(job))
        if status is None:
            return True
        decision, already_applied = status
        return not already_applied and decision not in {"SKIP", "EXTREME STRETCH"}

    return JobBuckets(
        apply_jobs=[job for job in buckets.apply_jobs if is_actionable(job)],
        maybe_jobs=[job for job in buckets.maybe_jobs if is_actionable(job)],
        needs_freshness_verification=[
            job for job in buckets.needs_freshness_verification if is_actionable(job)
        ],
    )


def merge_current_and_carry_forward_jobs(
    current_jobs: list[InspectionJob],
    carry_forward_jobs: list[InspectionJob],
) -> list[InspectionJob]:
    """Keep carry-forward jobs only when current discovery did not resolve that posting."""
    current_identities = {job_identity_key(job) for job in current_jobs}
    return [
        *current_jobs,
        *[
            job
            for job in carry_forward_jobs
            if job_identity_key(job) not in current_identities
        ],
    ]


def is_freshness_verification_candidate(job: InspectionJob) -> bool:
    if job.fit_score < MIN_APPLY_FIT_SCORE:
        return False
    if job.decision == NEEDS_FRESHNESS_VERIFICATION_DECISION:
        return True
    if job.decision != "Skip" or job.fit_score < MIN_FIT_SCORE_FOR_FRESHNESS_VERIFICATION:
        return False
    return any("posting date unknown" in flag.casefold() for flag in job.risk_flags)


def override_matches_job(job: InspectionJob, override: dict[str, str]) -> bool:
    company = override.get("company", "")
    title_contains = override.get("title_contains", "")
    url_contains = override.get("url_contains", "")
    job_id = override.get("job_id", "")
    if company and normalize_text(company) != normalize_text(job.company):
        return False
    if title_contains and normalize_match_text(title_contains) not in normalize_match_text(job.title):
        return False
    if url_contains and url_contains not in job.url:
        return False
    if job_id and job_id != job.job_id and job_id not in job.url:
        return False
    return bool(company or title_contains or url_contains or job_id)


def filter_overridden_jobs(
    jobs: list[InspectionJob],
    overrides: list[dict[str, str]],
    *,
    as_of: dt.date | None = None,
) -> list[InspectionJob]:
    if not overrides:
        return jobs
    effective_date = as_of or dt.date.today()
    return [
        job
        for job in jobs
        if not any(
            override_is_active(override, as_of=effective_date)
            and str(override.get("decision", "")).casefold() == "skip"
            and override_matches_job(job, override)
            for override in overrides
        )
    ]


def select_job_buckets(jobs: list[dict[str, object]] | list[InspectionJob]) -> JobBuckets:
    normalized_jobs = [
        enforce_apply_fit_threshold(job if isinstance(job, InspectionJob) else job_from_dict(job))
        for job in jobs
    ]
    ordered_jobs = sorted(normalized_jobs, key=job_sort_key)

    apply_jobs = collect_bucket_jobs(
        ordered_jobs,
        target_decision="Apply",
        per_company_limit=MAX_APPLY_ROWS_PER_COMPANY,
        total_limit=None,
    )
    maybe_jobs = collect_bucket_jobs(
        ordered_jobs,
        target_decision="Maybe",
        per_company_limit=MAX_MAYBE_ROWS_PER_COMPANY,
        total_limit=MAX_TOTAL_MAYBE_ROWS,
    )
    maybe_jobs = exclude_jobs_already_in_higher_bucket(maybe_jobs, apply_jobs)
    freshness_candidates = collect_custom_bucket_jobs(
        ordered_jobs,
        include_predicate=is_freshness_verification_candidate,
        per_company_limit=MAX_MAYBE_ROWS_PER_COMPANY,
        total_limit=MAX_TOTAL_VERIFICATION_ROWS,
    )
    return JobBuckets(
        apply_jobs=apply_jobs,
        maybe_jobs=maybe_jobs,
        needs_freshness_verification=freshness_candidates,
    )


def exclude_jobs_already_in_higher_bucket(
    jobs: list[InspectionJob],
    higher_bucket_jobs: list[InspectionJob],
) -> list[InspectionJob]:
    higher_titles = {dedupe_key(job) for job in higher_bucket_jobs}
    higher_identities = {job_identity_key(job) for job in higher_bucket_jobs}
    return [
        job
        for job in jobs
        if dedupe_key(job) not in higher_titles and job_identity_key(job) not in higher_identities
    ]


def enforce_apply_fit_threshold(job: InspectionJob) -> InspectionJob:
    if job.decision != "Apply" or job.fit_score >= MIN_APPLY_FIT_SCORE:
        return job
    return InspectionJob(
        company=job.company,
        title=job.title,
        job_id=job.job_id,
        posted_on=job.posted_on,
        posted_age_days=job.posted_age_days,
        location=job.location,
        work_model=job.work_model,
        fit_score=job.fit_score,
        decision="Maybe",
        source=job.source,
        url=job.url,
        risk_flags=job.risk_flags,
    )


def job_from_dict(item: dict[str, object]) -> InspectionJob:
    return InspectionJob(
        company=str(item.get("company", "")),
        title=str(item.get("title", "")),
        job_id=str(item.get("job_id", "")),
        posted_on=str(item.get("posted_on", "")),
        posted_age_days=coerce_optional_int(item.get("posted_age_days")),
        location=str(item.get("location", "")),
        work_model=str(item.get("work_model", "")),
        fit_score=coerce_int(item.get("fit_score")),
        decision=str(item.get("decision", "")),
        source=str(item.get("source", "")),
        url=str(item.get("url", "")),
        risk_flags=tuple(str(flag) for flag in item.get("risk_flags", []) if str(flag).strip()),
    )


def collect_bucket_jobs(
    jobs: list[InspectionJob],
    *,
    target_decision: str,
    per_company_limit: int,
    total_limit: int | None,
) -> list[InspectionJob]:
    return collect_custom_bucket_jobs(
        jobs,
        include_predicate=lambda job: job.decision == target_decision,
        per_company_limit=per_company_limit,
        total_limit=total_limit,
    )


def collect_custom_bucket_jobs(
    jobs: list[InspectionJob],
    *,
    include_predicate: Any,
    per_company_limit: int,
    total_limit: int | None,
) -> list[InspectionJob]:
    selected: list[InspectionJob] = []
    seen_title_keys: set[tuple[str, str]] = set()
    seen_job_identity_keys: set[tuple[str, str]] = set()
    company_counts: dict[str, int] = {}

    for job in jobs:
        if not include_predicate(job):
            continue
        title_key = dedupe_key(job)
        identity_key = job_identity_key(job)
        if title_key in seen_title_keys or identity_key in seen_job_identity_keys:
            continue
        company_key = normalize_text(job.company)
        company_count = company_counts.get(company_key, 0)
        if company_count >= per_company_limit:
            continue
        selected.append(job)
        seen_title_keys.add(title_key)
        seen_job_identity_keys.add(identity_key)
        company_counts[company_key] = company_count + 1
        if total_limit is not None and len(selected) >= total_limit:
            break
    return selected


def write_inspection_queue(
    *,
    discovered_jobs_path: Path,
    out_dir: Path,
    run_date: str,
    job_overrides_path: Path | None = None,
) -> Path:
    jobs = merge_current_and_carry_forward_jobs(
        load_jobs(discovered_jobs_path),
        load_carry_forward_jobs(out_dir, run_date),
    )
    overrides = load_job_overrides(job_overrides_path) if job_overrides_path else []
    jobs = filter_overridden_jobs(jobs, overrides, as_of=dt.date.fromisoformat(run_date))
    buckets = select_job_buckets(jobs)
    output_path = out_dir / f"{run_date}-job-inspection-queue.md"

    lines: list[str] = [
        f"# {run_date} - Job Inspection Queue",
        "",
        f"Source: `{discovered_jobs_path}`",
        "",
        "This artifact is the deterministic broad queue built from machine-scored ATS and verified public-board results.",
        "Use it as diagnostic input to the strict reviewer. It is not an application or resume-authorization queue.",
        "",
        f"## {DISCOVERY_APPLY_SECTION}",
        "",
        "| Priority | Company | Role | Posted | Location / Work Model | Fit | Source | URL |",
        "|---|---|---|---|---|---:|---|---|",
    ]
    lines.extend(format_job_rows(buckets.apply_jobs))
    lines.extend(
        [
            "",
            f"## {DISCOVERY_MAYBE_SECTION}",
            "",
            "| Priority | Company | Role | Posted | Location / Work Model | Fit | Source | URL |",
            "|---|---|---|---|---|---:|---|---|",
        ]
    )
    lines.extend(format_job_rows(buckets.maybe_jobs))
    lines.extend(
        [
            "",
            "## Needs Freshness Verification",
            "",
            "| Priority | Company | Role | Public Freshness Signal | Location / Work Model | Fit | Reason | URL |",
            "|---|---|---|---|---|---:|---|---|",
        ]
    )
    lines.extend(format_verification_rows(buckets.needs_freshness_verification))
    lines.extend(
        [
            "",
            "## Notes",
            "",
            f"- Apply rows are capped at {MAX_APPLY_ROWS_PER_COMPANY} per company after dedupe.",
            f"- Maybe rows are capped at {MAX_MAYBE_ROWS_PER_COMPANY} per company and {MAX_TOTAL_MAYBE_ROWS} overall.",
            "- Roles without a visible official posting date are treated as freshest for scoring and ranking, while the displayed date remains honest.",
            "- Needs Freshness Verification contains only strong-fit live official postings that need manual freshness judgment.",
            "- Keep this queue broad as a diagnostic artifact; the strict workflow report is the actionable queue.",
        ]
    )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def write_daily_report_from_buckets(
    *,
    buckets: JobBuckets,
    report_path: Path,
    run_date: str,
) -> Path:
    human_search_section = preserved_human_search_section(report_path)
    lines: list[str] = [
        f"# Daily Job Search Report - {run_date}",
        "",
        f"## {DISCOVERY_APPLY_SECTION}",
        "",
        "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
        "|---|---|---|---|---|---:|---|---|",
    ]
    lines.extend(format_daily_job_rows(buckets.apply_jobs))
    lines.extend(
        [
            "",
            f"## {DISCOVERY_MAYBE_SECTION}",
            "",
            "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
            "|---|---|---|---|---|---:|---|---|",
        ]
    )
    lines.extend(format_daily_job_rows(buckets.maybe_jobs))
    lines.extend(
        [
            "",
            "## Skip",
            "",
            "| Company | Role | Reason | URL |",
            "|---|---|---|---|",
            "",
            "## Needs Freshness Verification",
            "",
            "| Priority | Company | Role | Public Freshness Signal | Location / Work Model | Fit | Reason | URL |",
            "|---|---|---|---|---|---:|---|---|",
        ]
    )
    lines.extend(format_verification_rows(buckets.needs_freshness_verification))
    lines.extend(
        [
            "",
            "## Gap Questions for Candidate",
            "",
            "- None from the deterministic discovery queue.",
            "",
            "## Notes",
            "",
            "- Discovery candidates come from verified official postings and the unresolved carry-forward queue.",
            "- Candidate tables are synchronized after strict review; rejected and already-applied roles are removed.",
            "- Higher Signal requires a source fit score greater than 75; it is not resume authorization.",
            "- No resume or cover-letter artifacts are generated by the daily discovery run.",
        ]
    )
    if human_search_section:
        lines.extend(["", *human_search_section])

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def preserved_human_search_section(report_path: Path) -> list[str]:
    if not report_path.exists():
        return []
    lines = report_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "## Human Search Calibration":
            section = lines[index:]
            for marker_index, section_line in enumerate(section):
                if section_line.strip() == "<!-- networking-pass:start -->":
                    return section[:marker_index]
            return section
    return []


def format_daily_job_rows(jobs: list[InspectionJob]) -> list[str]:
    if not jobs:
        return ["| - | - | No jobs in this bucket | - | - | - | - |"]
    rows: list[str] = []
    for index, job in enumerate(jobs, start=1):
        posted = job.posted_on or freshness_label(job.posted_age_days)
        rows.append(
            f"| {index} | {escape_md(job.company)} | {escape_md(job.title)} | "
            f"{escape_md(posted)} | {escape_md(job.location)} / {escape_md(job.work_model)} | "
            f"{job.fit_score} | {job.url} | No |"
        )
    return rows


def format_job_rows(jobs: list[InspectionJob]) -> list[str]:
    if not jobs:
        return ["| - | - | No jobs in this bucket | - | - | - | - | - |"]
    lines: list[str] = []
    for index, job in enumerate(jobs, start=1):
        posted = job.posted_on or freshness_label(job.posted_age_days)
        lines.append(
            f"| {index} | {escape_md(job.company)} | {escape_md(job.title)} | "
            f"{escape_md(posted)} | {escape_md(job.location)} / {escape_md(job.work_model)} | "
            f"{job.fit_score} | {escape_md(job.source)} | {job.url} |"
        )
    return lines


def format_verification_rows(jobs: list[InspectionJob]) -> list[str]:
    if not jobs:
        return ["| - | - | No roles need freshness verification | - | - | - | - | - |"]
    lines: list[str] = []
    for index, job in enumerate(jobs, start=1):
        reason = "; ".join(job.risk_flags[:2]) or "official posting date unknown"
        lines.append(
            f"| {index} | {escape_md(job.company)} | {escape_md(job.title)} | "
            f"{escape_md(freshness_label(job.posted_age_days))} | {escape_md(job.location)} / {escape_md(job.work_model)} | "
            f"{job.fit_score} | {escape_md(reason)} | {job.url} |"
        )
    return lines


def freshness_label(posted_age_days: int | None) -> str:
    if posted_age_days is None:
        return "Date unavailable (treated as fresh)"
    if posted_age_days <= 7:
        return "Posted within 7 days"
    if posted_age_days <= 21:
        return "Posted within 21 days"
    if posted_age_days <= 60:
        return "Posted within 60 days"
    return f"Posted {posted_age_days} days ago"


def escape_md(value: str) -> str:
    return value.replace("|", "\\|")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic broad inspection queue from discovered jobs.")
    parser.add_argument("--discovered-jobs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument("--job-overrides", default="job_overrides.yml", type=Path)
    parser.add_argument("--daily-report", type=Path)
    parser.add_argument("--workflow-review", type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    output_path = write_inspection_queue(
        discovered_jobs_path=args.discovered_jobs,
        out_dir=args.out,
        run_date=args.date,
        job_overrides_path=args.job_overrides,
    )
    if args.daily_report is not None:
        jobs = merge_current_and_carry_forward_jobs(
            load_jobs(args.discovered_jobs),
            load_carry_forward_jobs(args.out, args.date),
        )
        overrides = load_job_overrides(args.job_overrides)
        buckets = select_job_buckets(
            filter_overridden_jobs(jobs, overrides, as_of=dt.date.fromisoformat(args.date))
        )
        if args.workflow_review is not None:
            buckets = filter_buckets_by_strict_review(
                buckets,
                load_strict_review_statuses(args.workflow_review),
            )
        write_daily_report_from_buckets(
            buckets=buckets,
            report_path=args.daily_report,
            run_date=args.date,
        )
    print(f"Wrote inspection queue: {output_path}")


if __name__ == "__main__":
    main()
