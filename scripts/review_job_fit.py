#!/usr/bin/env python3
"""
Strict post-discovery workflow reviewer for the candidate's jobagent.

Runs after ATS/public-board discovery and before resume work. It consumes the
normalized discovered-jobs JSON produced by discover_jobs.py and creates:

- YYYY-MM-DD-workflow-review.json
- YYYY-MM-DD-workflow-review.html
- YYYY-MM-DD-human-review-packets.md
- YYYY-MM-DD-resume-ready-jobs.md

Only roles in resume-ready-jobs.md should proceed to Word resume generation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

if __package__:
    from scripts.job_override_dates import override_is_active
    from scripts.language_requirements import required_unsupported_spoken_languages
    from scripts.location_requirements import (
        explicit_nonremote_location_outside_allowed_metro,
        hybrid_location_outside_allowed_metro,
    )
    from scripts.mark_applied_jobs import ReportJob, TrackerJob, load_tracker_jobs_snapshot, match_tracker_job
    from scripts.render_workflow_review_html import write_workflow_review_html
else:
    try:
        from scripts.job_override_dates import override_is_active
    except ModuleNotFoundError:
        from job_override_dates import override_is_active
    try:
        from scripts.language_requirements import required_unsupported_spoken_languages
    except ModuleNotFoundError:
        from language_requirements import required_unsupported_spoken_languages
    try:
        from scripts.location_requirements import (
            explicit_nonremote_location_outside_allowed_metro,
            hybrid_location_outside_allowed_metro,
        )
    except ModuleNotFoundError:
        from location_requirements import (
            explicit_nonremote_location_outside_allowed_metro,
            hybrid_location_outside_allowed_metro,
        )
    from mark_applied_jobs import ReportJob, TrackerJob, load_tracker_jobs_snapshot, match_tracker_job
    from render_workflow_review_html import write_workflow_review_html


REQUIRED_MARKERS = (
    "required",
    "must have",
    "minimum qualification",
    "basic qualification",
    "qualified candidates will have",
    "requirements",
    "experience with",
    "hands-on",
    "proficient with",
    "proficiency with",
    "technical expertise",
)

PRIMARY_STACK_MARKERS = (
    "primary stack",
    "our stack",
    "you will work primarily",
    "day-to-day",
    "hands-on development",
    "build and maintain",
)

ALLOWED_GAP_RESOLUTIONS = {
    "do_not_claim",
    "limited_exposure",
    "professional_evidence",
    "posting_false_positive",
}

TECHNICAL_SCOPE_MARKERS = (
    "architecture",
    "architectural",
    "technical direction",
    "platform ownership",
    "modernization",
    "mentor",
    "design review",
    "production ownership",
    "production reliability",
    "cross-team",
    "engineering execution",
    "technical delivery",
    "application development",
    "application development leadership",
    "platform engineering",
    "backend modernization",
    "cloud modernization",
    "engineering leadership",
)

PRINCIPAL_ARCHITECT_TITLE_MARKERS = (
    "principal",
    "staff",
    "staff software engineer",
    "staff engineer",
    "software architect",
    "technical architect",
    "lead architect",
    "platform architect",
    "solution architect",
    "solutions architect",
    "cpq architect",
    "delivery architect",
)

COMPENSATION_CONTEXT_MARKERS = (
    "salary",
    "compensation",
    "base pay",
    "pay range",
    "annual pay",
)

COMPENSATION_AMOUNT_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:usd\s*|\$\s*)?"
    r"(?P<amount>\d{2,3}(?:,\d{3})+(?:\.\d+)?|\d{5,6}(?:\.\d+)?|\d+(?:\.\d+)?\s*k)"
    r"(?![a-z0-9])",
    re.IGNORECASE,
)

REDUCED_SCOPE_INTERVIEW_NOTE = (
    "Use reduced-scope interview positioning. Emphasize hands-on contribution, team fit, "
    "production problem-solving, code quality, mentoring where useful, and willingness to "
    "operate within the role's scope. Do not over-answer every question at principal/architect "
    "level unless asked. Prepared statement: \"I have operated at a principal level, but I'm "
    "not looking for title inflation. I'm looking for the right technical environment. If the "
    "role needs someone who can write code, review designs, troubleshoot production issues, "
    "and mentor engineers without needing a management title, that is a good fit. If the scope "
    "is purely mid-level feature delivery with no ownership, then it may not be the right match.\""
)


@dataclass
class WorkflowReview:
    company: str
    title: str
    job_id: str
    url: str
    location: str
    work_model: str
    source_decision: str
    source_fit_score: int
    workflow_decision: str
    should_generate_resume: bool
    recommended_resume_base: str
    overqualified_risk: str
    resume_positioning: str
    gap_questions_needed: bool
    already_applied: bool = False
    applied_status: str = ""
    applied_date: str = ""
    hard_blockers: list[str] = field(default_factory=list)
    required_gaps: list[str] = field(default_factory=list)
    preferred_gaps: list[str] = field(default_factory=list)
    resolved_gaps: dict[str, str] = field(default_factory=dict)
    direct_matches: list[str] = field(default_factory=list)
    questions_for_candidate: list[str] = field(default_factory=list)
    final_action: str = ""
    risk_flags: list[str] = field(default_factory=list)
    seniority_scope_risks: list[str] = field(default_factory=list)
    interview_positioning_note: str = ""


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    if yaml is not None:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        data = load_yaml_via_ruby(path)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def load_job_overrides(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    payload = load_yaml(path)
    raw_overrides = payload.get("overrides", [])
    if not isinstance(raw_overrides, list):
        raise ValueError(f"Expected 'overrides' list in {path}")

    overrides: list[dict[str, str]] = []
    for item in raw_overrides:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid job override entry in {path}: {item!r}")
        overrides.append(
            {
                str(key): str(value).strip()
                for key, value in item.items()
                if value is not None and str(value).strip()
            }
        )
    return overrides


def load_yaml_via_ruby(path: Path) -> dict[str, Any] | list[Any]:
    ruby = shutil.which("ruby")
    if not ruby:
        raise ModuleNotFoundError(
            "PyYAML is not installed and Ruby is not available for YAML fallback parsing."
        )

    result = subprocess.run(
        [
            ruby,
            "-ryaml",
            "-rjson",
            "-e",
            "print JSON.generate(YAML.load_file(ARGV[0]))",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed: dict[str, Any] | list[Any] = json.loads(result.stdout)
    return parsed


def load_jobs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError(f"Expected top-level 'jobs' list in {path}")
    return [job for job in jobs if isinstance(job, dict)]


def job_identity(item: dict[str, Any]) -> tuple[str, str]:
    company = normalize(item.get("company", ""))
    job_id = normalize(item.get("job_id", ""))
    if job_id:
        return company, f"job-id:{job_id}"
    url = str(item.get("url", "")).strip()
    if url:
        return company, url
    return company, f"title:{normalize(item.get('title', ''))}"


def is_active_carry_forward_review(item: dict[str, Any]) -> bool:
    if bool(item.get("already_applied", False)):
        return False
    if bool(item.get("should_generate_resume", False)):
        return True
    decision = normalize(item.get("workflow_decision", ""))
    return bool(item.get("gap_questions_needed", False)) and decision in {
        "strong apply",
        "apply",
        "moderate stretch",
    }


def load_carry_forward_jobs(*, out_dir: Path, run_date: str) -> list[dict[str, Any]]:
    """Load the latest report's still-active, already-reviewed source jobs.

    Carry-forward is based on strict workflow state, not broad discovery labels.
    The prior output embeds its reviewed source jobs; older outputs fall back to
    the matching discovered-jobs JSON for backward compatibility.
    """
    candidates = [
        path
        for path in out_dir.glob("????-??-??-workflow-review.json")
        if path.name[:10] < run_date
    ]
    if not candidates:
        return []

    latest_path = max(candidates, key=lambda path: path.name[:10])
    payload: object = json.loads(latest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected top-level object in {latest_path}")
    raw_reviews = payload.get("reviews")
    if not isinstance(raw_reviews, list) or any(not isinstance(item, dict) for item in raw_reviews):
        raise ValueError(f"Expected reviews list of objects in {latest_path}")

    active_reviews = [
        item
        for item in raw_reviews
        if isinstance(item, dict) and is_active_carry_forward_review(item)
    ]
    active_identities = {job_identity(item) for item in active_reviews}
    active_urls = {
        (normalize(item.get("company", "")), str(item.get("url", "")).strip())
        for item in active_reviews
        if str(item.get("url", "")).strip()
    }
    if not active_identities:
        return []

    raw_source_jobs = payload.get("source_jobs")
    if raw_source_jobs is None:
        discovered_path = latest_path.with_name(
            latest_path.name.replace("-workflow-review.json", "-discovered-jobs.json")
        )
        source_jobs = load_jobs(discovered_path) if discovered_path.exists() else []
    else:
        if not isinstance(raw_source_jobs, list) or any(not isinstance(item, dict) for item in raw_source_jobs):
            raise ValueError(f"Expected source_jobs list of objects in {latest_path}")
        source_jobs = [item for item in raw_source_jobs if isinstance(item, dict)]

    return [
        job
        for job in source_jobs
        if job_identity(job) in active_identities
        or (
            normalize(job.get("company", "")),
            str(job.get("url", "")).strip(),
        ) in active_urls
    ]


def merge_current_and_carry_forward_jobs(
    current_jobs: list[dict[str, Any]],
    carry_forward_jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_requisitions: set[tuple[str, str]] = set()
    seen_urls: set[tuple[str, str]] = set()
    seen_fallbacks: set[tuple[str, str]] = set()
    for job in [*current_jobs, *carry_forward_jobs]:
        company = normalize(job.get("company", ""))
        job_id = normalize(job.get("job_id", ""))
        url = str(job.get("url", "")).strip().casefold()
        requisition_identity = (company, job_id) if job_id else None
        url_identity = (company, url) if url else None
        fallback_identity = job_identity(job) if not job_id and not url else None
        if requisition_identity is not None and requisition_identity in seen_requisitions:
            continue
        if url_identity is not None and url_identity in seen_urls:
            continue
        if fallback_identity is not None and fallback_identity in seen_fallbacks:
            continue
        merged.append(job)
        if requisition_identity is not None:
            seen_requisitions.add(requisition_identity)
        if url_identity is not None:
            seen_urls.add(url_identity)
        if fallback_identity is not None:
            seen_fallbacks.add(fallback_identity)
    return merged


def dedupe_equivalent_postings(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one posting when company, role, location, model, and description match."""
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for job in jobs:
        description = normalize(job.get("description", ""))
        if not description:
            deduped.append(job)
            continue
        key = (
            normalize(job.get("company", "")),
            normalize(job.get("title", "")),
            normalize(job.get("location", "")),
            normalize(job.get("work_model", "")),
            description,
        )
        if key in seen:
            continue
        deduped.append(job)
        seen.add(key)
    return deduped


def normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def normalize_title_marker(value: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value).casefold())).strip()


def is_excluded_company(job: dict[str, Any], workflow_profile: dict[str, Any]) -> bool:
    excluded = workflow_profile.get("excluded_companies", [])
    if not isinstance(excluded, list):
        return False
    company = normalize(job.get("company", ""))
    return company in {normalize(item) for item in excluded if normalize(item)}


def is_excluded_role_title(job: dict[str, Any], workflow_profile: dict[str, Any]) -> bool:
    title = normalize_title_marker(job.get("title", ""))
    for configured_marker in workflow_profile.get("excluded_role_title_markers", []) or []:
        marker = normalize_title_marker(configured_marker)
        if not marker:
            raise ValueError("Excluded role title markers must be non-empty strings")
        if re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", title):
            return True
    return False


def is_skipped_by_job_override(
    job: dict[str, Any],
    overrides: list[dict[str, str]],
    *,
    as_of: dt.date | None = None,
) -> bool:
    effective_date = as_of or dt.date.today()
    job_company = normalize(job.get("company", ""))
    job_title = normalize_title_marker(job.get("title", ""))
    job_url = str(job.get("url", ""))
    job_id = str(job.get("job_id", ""))
    for override in overrides:
        if not override_is_active(override, as_of=effective_date):
            continue
        if normalize(override.get("decision", "")) != "skip":
            continue
        company = normalize(override.get("company", ""))
        title_contains = normalize_title_marker(override.get("title_contains", ""))
        url_contains = override.get("url_contains", "")
        override_job_id = override.get("job_id", "")
        if company and company != job_company:
            continue
        if title_contains and title_contains not in job_title:
            continue
        if url_contains and url_contains not in job_url:
            continue
        if override_job_id and override_job_id != job_id and override_job_id not in job_url:
            continue
        if company or title_contains or url_contains or override_job_id:
            return True
    return False


def contains_term(text: str, token: str) -> bool:
    normalized_token = normalize(token)
    if not normalized_token:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(normalized_token)}(?![a-z0-9])"
    return re.search(pattern, normalize(text)) is not None


def contains_any_term(text: str, tokens: list[str]) -> bool:
    return any(contains_term(text, token) for token in tokens)


def text_for_job(job: dict[str, Any]) -> str:
    pieces = [
        job.get("title", ""),
        job.get("description", ""),
        job.get("location", ""),
        job.get("work_model", ""),
        " ".join(str(flag) for flag in job.get("risk_flags", []) or []),
    ]
    return normalize(" ".join(str(piece) for piece in pieces))


def contains_required_context(text: str, token: str) -> bool:
    token = normalize(token)
    if not token:
        return False
    for marker in REQUIRED_MARKERS:
        for match in re.finditer(re.escape(marker), text):
            if marker == "requirements":
                # A Requirements section introduces skills that follow it. Generic prose such as
                # "meet enterprise security requirements" must not make nearby tools mandatory.
                window = text[match.start(): match.end() + 320]
            else:
                window = text[max(0, match.start() - 180): match.end() + 220]
            if contains_term(window, token):
                return True
    return False


def contains_primary_context(text: str, token: str) -> bool:
    token = normalize(token)
    if not token:
        return False
    for marker in PRIMARY_STACK_MARKERS:
        for match in re.finditer(re.escape(marker), text):
            window = text[max(0, match.start() - 160): match.end() + 240]
            if contains_term(window, token):
                return True
    return False


def skill_variants(skill: str) -> list[str]:
    lowered = normalize(skill)
    variants = {lowered}
    mapping = {
        "rest apis": ["rest api", "restful api", "restful apis"],
        "typescript professional experience": ["typescript"],
        "python backend professional experience": ["python"],
        "ruby on rails": ["ruby on rails", "rails"],
        "salesforce apex": ["apex"],
        "aws": ["aws", "amazon web services"],
        "azure": ["azure"],
        "gcp": ["gcp", "google cloud"],
        "nosql": ["nosql", "mongo", "mongodb"],
        "mcp servers": ["mcp", "model context protocol"],
    }
    for key, values in mapping.items():
        if lowered == normalize(key):
            variants.update(normalize(value) for value in values)
    return [value for value in variants if value]


def collect_matches(text: str, values: list[str]) -> list[str]:
    hits: list[str] = []
    for value in values:
        for variant in skill_variants(value):
            if contains_term(text, variant):
                hits.append(value)
                break
    return dedupe(hits)


def required_skill_has_confirmed_disjunctive_alternative(
    text: str,
    required_skill: str,
    confirmed_strengths: list[str],
) -> bool:
    normalized_text = normalize(text)
    for clause in re.split(r"(?<=[.;])\s+", normalized_text):
        if not contains_term(clause, required_skill) or not contains_term(clause, "or"):
            continue
        for strength in confirmed_strengths:
            for variant in skill_variants(strength):
                if normalize(variant) == normalize(required_skill):
                    continue
                if contains_term(clause, variant):
                    return True
    return False


def detect_required_gaps(
    text: str,
    do_not_claim: list[str],
    confirmed_strengths: list[str] | None = None,
) -> list[str]:
    required_text = re.split(
        r"\b(?:preferred qualifications|preferred experience|nice to have|bonus qualifications)\b",
        text,
        maxsplit=1,
    )[0]
    confirmed = confirmed_strengths or []
    gaps: list[str] = []
    for skill in do_not_claim:
        required_variants = [
            variant
            for variant in skill_variants(skill)
            if contains_required_context(required_text, variant)
        ]
        if required_variants and not any(
            required_skill_has_confirmed_disjunctive_alternative(
                required_text,
                variant,
                confirmed,
            )
            for variant in required_variants
        ):
            gaps.append(skill)
    return dedupe(gaps)


def detect_preferred_gaps(
    text: str,
    do_not_claim: list[str],
    required_gaps: list[str],
    confirmed_strengths: list[str] | None = None,
) -> list[str]:
    required_set = {normalize(item) for item in required_gaps}
    confirmed = confirmed_strengths or []
    gaps: list[str] = []
    for skill in do_not_claim:
        if normalize(skill) in required_set:
            continue
        for variant in skill_variants(skill):
            if contains_term(text, variant) and not required_skill_has_confirmed_disjunctive_alternative(
                text,
                variant,
                confirmed,
            ):
                gaps.append(skill)
                break
    return dedupe(gaps)


def gap_resolution_status(answer: str) -> str:
    status = normalize(answer.split(":", 1)[0]).replace(" ", "_")
    if status not in ALLOWED_GAP_RESOLUTIONS:
        raise ValueError(
            f"Unsupported gap resolution {answer!r}; expected one of {sorted(ALLOWED_GAP_RESOLUTIONS)}"
        )
    return status


def resolve_gap_answers(
    *, job: dict[str, Any], workflow_profile: dict[str, Any], detected_gaps: list[str]
) -> dict[str, str]:
    candidate_answers = workflow_profile.get("candidate_gap_resolutions", {}) or {}
    if not isinstance(candidate_answers, dict):
        raise ValueError("workflow_profile candidate_gap_resolutions must be a skill-to-answer mapping")

    configured = workflow_profile.get("gap_resolutions", {}) or {}
    if not isinstance(configured, dict):
        raise ValueError("workflow_profile gap_resolutions must be a URL-to-answers mapping")
    job_url = str(job.get("url", "")).strip()
    raw_answers = configured.get(job_url, {})
    if not isinstance(raw_answers, dict):
        raise ValueError(f"Gap resolutions for {job_url!r} must be a skill-to-answer mapping")

    normalized_answers: dict[str, str] = {}
    for skill, raw_answer in candidate_answers.items():
        if not isinstance(skill, str) or not isinstance(raw_answer, str) or not raw_answer.strip():
            raise ValueError(
                "Candidate gap resolutions require non-empty string skills and answers"
            )
        key = normalize(skill)
        if key in normalized_answers:
            raise ValueError(f"Duplicate candidate gap resolution for {skill!r}")
        answer = raw_answer.strip()
        status = gap_resolution_status(answer)
        if status == "posting_false_positive":
            raise ValueError(
                f"Candidate gap resolution for {skill!r} cannot use posting_false_positive"
            )
        normalized_answers[key] = answer

    for skill, raw_answer in raw_answers.items():
        if not isinstance(skill, str) or not isinstance(raw_answer, str) or not raw_answer.strip():
            raise ValueError(f"Gap resolutions for {job_url!r} require non-empty string skills and answers")
        key = normalize(skill)
        answer = raw_answer.strip()
        gap_resolution_status(answer)
        normalized_answers[key] = answer

    return {
        gap: normalized_answers[normalize(gap)]
        for gap in detected_gaps
        if normalize(gap) in normalized_answers
    }


def configured_salary_floor(workflow_profile: dict[str, Any]) -> float | None:
    raw_floor = workflow_profile.get("salary_floor")
    if raw_floor is None or raw_floor == "":
        return None
    if isinstance(raw_floor, bool) or not isinstance(raw_floor, (int, float, str)):
        raise ValueError("workflow_profile salary_floor must be a positive number")
    try:
        floor = float(str(raw_floor).replace(",", "").strip())
    except ValueError as exc:
        raise ValueError("workflow_profile salary_floor must be a positive number") from exc
    if floor <= 0:
        raise ValueError("workflow_profile salary_floor must be a positive number")
    return floor


def published_annual_compensation_maximum(text: str) -> float | None:
    normalized_text = normalize(text)
    amounts: list[float] = []
    for marker in COMPENSATION_CONTEXT_MARKERS:
        for marker_match in re.finditer(re.escape(marker), normalized_text):
            window_start = max(0, marker_match.start() - 80)
            window_end = min(len(normalized_text), marker_match.end() + 260)
            window = normalized_text[window_start:window_end]
            for amount_match in COMPENSATION_AMOUNT_PATTERN.finditer(window):
                nearby_start = max(0, amount_match.start() - 50)
                nearby_end = min(len(window), amount_match.end() + 50)
                nearby = window[nearby_start:nearby_end]
                if "per hour" in nearby or "hourly" in nearby:
                    continue
                token = amount_match.group("amount").replace(",", "").replace(" ", "")
                multiplier = 1000.0 if token.casefold().endswith("k") else 1.0
                numeric_token = token[:-1] if multiplier > 1 else token
                value = float(numeric_token) * multiplier
                if value >= 10000:
                    amounts.append(value)
    return max(amounts) if amounts else None


def detect_hard_blockers(
    text: str,
    job: dict[str, Any],
    workflow_profile: dict[str, Any],
    required_gaps: list[str],
    confirmed_strengths: list[str] | None = None,
) -> list[str]:
    blockers: list[str] = []
    risk_flags = [normalize(flag) for flag in job.get("risk_flags", []) or []]

    salary_floor = configured_salary_floor(workflow_profile)
    published_maximum = published_annual_compensation_maximum(text)
    if (
        salary_floor is not None
        and published_maximum is not None
        and published_maximum <= salary_floor
    ):
        blockers.append(
            "published compensation maximum "
            f"${published_maximum:,.0f} does not exceed ${salary_floor:,.0f} floor"
        )

    normalized_title = normalize_title_marker(job.get("title", ""))
    for configured_marker in workflow_profile.get("excluded_role_title_markers", []) or []:
        marker = normalize_title_marker(configured_marker)
        if not marker:
            raise ValueError("Excluded role title markers must be non-empty strings")
        if re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", normalized_title):
            blockers.append(f"excluded role type: {str(configured_marker).casefold()}")

    unsupported_languages = required_unsupported_spoken_languages(
        f"{job.get('title', '')} {text}",
        [str(item) for item in workflow_profile.get("unsupported_required_languages", [])],
    )
    blockers.extend(
        f"required spoken language unsupported: {language}"
        for language in unsupported_languages
    )

    if hybrid_location_outside_allowed_metro(
        location=str(job.get("location", "")),
        work_model=str(job.get("work_model", "")),
        allowed_metro_tokens=[
            str(item) for item in workflow_profile.get("hybrid_allowed_metro_tokens", [])
        ],
    ):
        blockers.append("hybrid/flex location outside Atlanta metro")

    if explicit_nonremote_location_outside_allowed_metro(
        location=str(job.get("location", "")),
        work_model=str(job.get("work_model", "")),
        allowed_metro_tokens=[
            str(item) for item in workflow_profile.get("hybrid_allowed_metro_tokens", [])
        ],
    ):
        blockers.append("physical location outside Atlanta metro without remote status")

    for configured in workflow_profile.get("hard_reject_if_primary", []) or []:
        configured_text = normalize(configured)
        if "react" in configured_text and contains_primary_context(text, "react"):
            blockers.append(str(configured))
        elif "angular" in configured_text and contains_primary_context(text, "angular"):
            blockers.append(str(configured))
        elif "vue" in configured_text and contains_primary_context(text, "vue"):
            blockers.append(str(configured))
        elif "ruby" in configured_text and (contains_term(text, "ruby on rails") or contains_primary_context(text, "rails")):
            blockers.append(str(configured))
        elif (
            configured_text.startswith("go ")
            and contains_required_context(text, "go")
            and not required_skill_has_confirmed_disjunctive_alternative(
                text,
                "go",
                confirmed_strengths or [],
            )
        ):
            blockers.append(str(configured))
        elif "python primary" in configured_text and contains_primary_context(text, "python"):
            blockers.append(str(configured))
        elif "salesforce platform" in configured_text and contains_any_term(text, ["apex", "lwc", "salesforce platform"]):
            blockers.append(str(configured))
        elif "aws/terraform/eks" in configured_text and contains_any_term(text, ["terraform", "eks"]) and contains_required_context(text, "aws"):
            blockers.append(str(configured))
        elif "active clearance" in configured_text and any("clearance" in flag for flag in risk_flags):
            blockers.append(str(configured))
        elif "west coast" in configured_text and "west coast" in text and contains_required_context(text, "office"):
            blockers.append(str(configured))
        elif "international-only" in configured_text and any("location likely incompatible" in flag for flag in risk_flags):
            blockers.append(str(configured))

    if len(required_gaps) >= 3:
        blockers.append("three or more required skill/domain gaps")

    if normalize(job.get("decision")) == "skip" and source_score(job) < 50:
        blockers.append("source scorer already classified as low-fit Skip")

    return dedupe(blockers)


def choose_template(text: str, title: str) -> str:
    combined = f"{normalize(title)} {text}"
    if contains_term(combined, "forward deployed"):
        return "consulting_architecture"
    if contains_any_term(combined, ["cpq", "quote-to-cash", "pricing", "configuration"]):
        if contains_any_term(combined, ["consultant", "implementation", "solution architect", "architect"]):
            return "cpq_implementation"
    if contains_any_term(
        combined,
        ["success architect", "customer architect", "technical account", "customer solutions", "solutions engineer", "sales engineer"],
    ):
        return "technical_success"
    if contains_any_term(combined, ["solution architect", "technical consultant", "professional services", "implementation architect", "delivery architect"]):
        return "consulting_architecture"
    return "software_engineering"


def is_strategic_company(company: str, workflow_profile: dict[str, Any]) -> bool:
    configured = workflow_profile.get("strategic_companies", []) or []
    if not isinstance(configured, list):
        raise ValueError("workflow_profile strategic_companies must be a list")
    strategic_names = {normalize(item) for item in configured if normalize(item)}
    return normalize(company) in strategic_names


def has_seniority_approval(job: dict[str, Any], workflow_profile: dict[str, Any]) -> bool:
    approvals = workflow_profile.get("seniority_approvals", {}) or {}
    if not isinstance(approvals, dict):
        raise ValueError("workflow_profile seniority_approvals must be a mapping keyed by posting URL")
    url = str(job.get("url", "")).strip()
    value = approvals.get(url, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return normalize(value) in {"approved", "true", "yes"}
    raise ValueError(f"Invalid seniority approval for {url or 'missing URL'}")


def technical_scope_hits(text: str) -> list[str]:
    return [marker for marker in TECHNICAL_SCOPE_MARKERS if contains_term(text, marker)]


def is_director_title(title: str) -> bool:
    return contains_term(normalize(title), "director")


def classify_seniority_fit(
    *,
    job: dict[str, Any],
    text: str,
    workflow_profile: dict[str, Any],
) -> tuple[str, str, list[str], str]:
    title = normalize(job.get("title", ""))
    scope_hits = technical_scope_hits(text)
    strategic = is_strategic_company(str(job.get("company", "")), workflow_profile)
    senior_or_lead = contains_term(title, "senior") or contains_term(title, "lead")
    principal_or_architect = any(contains_term(title, marker) for marker in PRINCIPAL_ARCHITECT_TITLE_MARKERS)
    director = is_director_title(title)

    if director:
        risk = "low" if scope_hits else "high"
        positioning = "director_technical_leadership"
    elif principal_or_architect:
        risk = "low"
        positioning = "principal_architect_forward"
    elif senior_or_lead:
        risk = "medium" if scope_hits or strategic else "high"
        positioning = "senior_lead_execution_forward"
    else:
        risk = "medium" if scope_hits or strategic else "high"
        positioning = "principal_architect_forward" if scope_hits else "senior_lead_execution_forward"

    scope_risks: list[str] = []
    if risk == "high":
        scope_risks.append(
            "Role scope may appear materially below the candidate's current Senior Principal level"
        )
        if not scope_hits:
            scope_risks.append(
                "Posting lacks architecture, modernization, production ownership, mentoring, or cross-team technical leadership"
            )
    elif risk == "medium" and senior_or_lead:
        label = "Lead" if contains_term(title, "lead") else "Senior"
        scope_risks.append(
            f"{label} title may still appear below the candidate's current Senior Principal level"
        )

    interview_note = REDUCED_SCOPE_INTERVIEW_NOTE if risk in {"medium", "high"} else ""
    return risk, positioning, scope_risks, interview_note


def source_score(job: dict[str, Any]) -> int:
    try:
        return int(job.get("fit_score") or 0)
    except (TypeError, ValueError):
        return 0


def classify_review(
    *,
    job: dict[str, Any],
    workflow_profile: dict[str, Any],
    tracker_jobs: list[TrackerJob] | None = None,
) -> WorkflowReview:
    text = text_for_job(job)
    do_not_claim = [str(item) for item in workflow_profile.get("do_not_claim", []) or []]
    confirmed = [str(item) for item in workflow_profile.get("confirmed_strengths", []) or []]

    required_gaps = detect_required_gaps(text, do_not_claim, confirmed)
    preferred_gaps = detect_preferred_gaps(text, do_not_claim, required_gaps, confirmed)
    resolved_gaps = resolve_gap_answers(
        job=job,
        workflow_profile=workflow_profile,
        detected_gaps=[*required_gaps, *preferred_gaps],
    )
    effective_required_gaps = [
        gap
        for gap in required_gaps
        if gap not in resolved_gaps
        or gap_resolution_status(resolved_gaps[gap]) not in {"professional_evidence", "posting_false_positive"}
    ]
    blockers = detect_hard_blockers(
        text,
        job,
        workflow_profile,
        effective_required_gaps,
        confirmed,
    )
    overqualified_risk, resume_positioning, seniority_scope_risks, interview_positioning_note = (
        classify_seniority_fit(job=job, text=text, workflow_profile=workflow_profile)
    )
    if is_director_title(str(job.get("title", ""))) and not technical_scope_hits(text):
        blockers.append("director role lacks clear technical ownership")
        blockers = dedupe(blockers)
    source_fit_reasons = job.get("fit_reasons", []) or []
    if not isinstance(source_fit_reasons, list):
        raise ValueError("Job fit_reasons must be a list")
    direct_match_text = f"{text} {' '.join(str(reason) for reason in source_fit_reasons)}"
    direct_matches = collect_matches(direct_match_text, confirmed)
    tracker_match = match_tracker_job(
        ReportJob(
            company=str(job.get("company", "")),
            title=str(job.get("title", "")),
            posted=str(job.get("posted", "")),
            location=str(job.get("location", "")),
            fit=str(source_score(job)),
            url=str(job.get("url", "")),
        ),
        tracker_jobs or [],
    )
    already_applied = bool(tracker_match and tracker_match.already_applied)

    fit_score = source_score(job)
    source_decision = str(job.get("decision", ""))
    template = choose_template(text, str(job.get("title", "")))

    if blockers:
        workflow_decision = "SKIP"
    elif effective_required_gaps:
        workflow_decision = "EXTREME STRETCH" if fit_score >= 60 else "SKIP"
    elif fit_score >= 88 and len(preferred_gaps) <= 2:
        workflow_decision = "STRONG APPLY"
    elif fit_score >= 76 and len(preferred_gaps) <= 4:
        workflow_decision = "APPLY"
    elif fit_score >= 60:
        workflow_decision = "MODERATE STRETCH"
    elif fit_score >= 45:
        workflow_decision = "EXTREME STRETCH"
    else:
        workflow_decision = "SKIP"

    unresolved_required_gaps = [gap for gap in required_gaps if gap not in resolved_gaps]
    unresolved_preferred_gaps = [gap for gap in preferred_gaps if gap not in resolved_gaps]
    questions = [] if already_applied else build_questions(
        unresolved_required_gaps,
        unresolved_preferred_gaps,
        workflow_decision,
    )
    gap_questions_needed = bool(questions)
    strategic_company = is_strategic_company(str(job.get("company", "")), workflow_profile)
    explicit_seniority_approval = has_seniority_approval(job, workflow_profile)
    resume_core_matches = {
        "java",
        "spring boot",
        "oracle cpq",
        "quote-to-cash",
        "professional services",
        "enterprise integrations",
        "customer-facing technical delivery",
        "technical leadership",
    }
    has_clear_resume_core = any(normalize(match) in resume_core_matches for match in direct_matches)
    strong_moderate_stretch = (
        workflow_decision == "MODERATE STRETCH"
        and fit_score >= 75
        and not unresolved_required_gaps
        and not unresolved_preferred_gaps
        and has_clear_resume_core
    )
    seniority_allows_resume = (
        overqualified_risk in {"low", "medium"}
        or strategic_company
        or explicit_seniority_approval
    )
    should_generate_resume = (
        (workflow_decision in {"STRONG APPLY", "APPLY"} or strong_moderate_stretch)
        and not gap_questions_needed
        and not already_applied
        and seniority_allows_resume
    )

    final_action = {
        "STRONG APPLY": "Run resume workflow now." if should_generate_resume else "Answer gap questions before resume workflow.",
        "APPLY": "Run resume workflow now." if should_generate_resume else "Answer gap questions before resume workflow.",
        "MODERATE STRETCH": "Review manually before resume workflow.",
        "EXTREME STRETCH": "Skip unless the candidate explicitly overrides.",
        "SKIP": "Skip.",
    }[workflow_decision]
    if already_applied:
        final_action = "Already applied — do not generate another resume."
    elif overqualified_risk == "high" and not seniority_allows_resume and workflow_decision in {
        "STRONG APPLY",
        "APPLY",
        "MODERATE STRETCH",
    }:
        final_action = "Hold resume workflow until the candidate explicitly approves the seniority/scope risk."

    return WorkflowReview(
        company=str(job.get("company", "")),
        title=str(job.get("title", "")),
        job_id=str(job.get("job_id", "")),
        url=str(job.get("url", "")),
        location=str(job.get("location", "")),
        work_model=str(job.get("work_model", "")),
        source_decision=source_decision,
        source_fit_score=fit_score,
        workflow_decision=workflow_decision,
        should_generate_resume=should_generate_resume,
        recommended_resume_base=(
            template if workflow_decision not in {"SKIP", "EXTREME STRETCH"} and not already_applied else "none"
        ),
        overqualified_risk=overqualified_risk,
        resume_positioning=resume_positioning,
        gap_questions_needed=gap_questions_needed,
        already_applied=already_applied,
        applied_status=tracker_match.status if tracker_match else "",
        applied_date=tracker_match.applied_date if tracker_match else "",
        hard_blockers=blockers,
        required_gaps=required_gaps,
        preferred_gaps=preferred_gaps[:12],
        resolved_gaps=resolved_gaps,
        direct_matches=direct_matches[:12],
        questions_for_candidate=questions,
        final_action=final_action,
        risk_flags=[str(flag) for flag in job.get("risk_flags", []) or []],
        seniority_scope_risks=seniority_scope_risks,
        interview_positioning_note=interview_positioning_note,
    )


def build_questions(required_gaps: list[str], preferred_gaps: list[str], decision: str) -> list[str]:
    questions: list[str] = []
    for gap in required_gaps[:8]:
        questions.append(f"Does the candidate have direct professional experience with required item: {gap}?")
    if decision in {"APPLY", "STRONG APPLY"}:
        for gap in preferred_gaps[:4]:
            questions.append(f"Can this be truthfully positioned or should it be avoided: {gap}?")
    elif decision == "MODERATE STRETCH":
        for gap in preferred_gaps[:6]:
            questions.append(f"Is this a blocker, secondary exposure, or avoid entirely: {gap}?")
    return questions


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        key = normalize(item)
        if not key or key in seen:
            continue
        output.append(item)
        seen.add(key)
    return output


def escape_md(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def write_json(
    reviews: list[WorkflowReview],
    out_dir: Path,
    run_date: str,
    *,
    source_jobs: list[dict[str, Any]] | None = None,
    current_source_job_count: int = 0,
    carry_forward_source_job_count: int = 0,
) -> Path:
    path = out_dir / f"{run_date}-workflow-review.json"
    payload = {
        "generated_on": run_date,
        "current_source_job_count": current_source_job_count,
        "carry_forward_source_job_count": carry_forward_source_job_count,
        "source_jobs": source_jobs or [],
        "reviews": [asdict(review) for review in reviews],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_resume_ready(reviews: list[WorkflowReview], out_dir: Path, run_date: str) -> Path:
    path = out_dir / f"{run_date}-resume-ready-jobs.md"
    rows = [
        f"# {run_date} - Resume-Ready Jobs",
        "",
        "Only jobs in this file should proceed to Word resume or cover-letter generation.",
        "",
        "| Priority | Company | Role | Decision | Template | Fit | Overqualified Risk | Resume Positioning | URL |",
        "|---:|---|---|---|---|---:|---|---|---|",
    ]
    ready = [review for review in reviews if review.should_generate_resume]
    ready.sort(key=lambda item: (-item.source_fit_score, item.company, item.title))
    if not ready:
        rows.append("| - | - | No jobs cleared strict resume workflow | - | - | - | - | - | - |")
    else:
        for index, review in enumerate(ready, start=1):
            rows.append(
                f"| {index} | {escape_md(review.company)} | {escape_md(review.title)} | "
                f"{review.workflow_decision} | {review.recommended_resume_base} | "
                f"{review.source_fit_score} | {review.overqualified_risk} | "
                f"{review.resume_positioning} | {review.url} |"
            )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def write_packets(reviews: list[WorkflowReview], out_dir: Path, run_date: str) -> Path:
    path = out_dir / f"{run_date}-human-review-packets.md"
    ordered = sorted(reviews, key=lambda item: (decision_rank(item.workflow_decision), -item.source_fit_score, item.company, item.title))
    lines = [f"# {run_date} - Human Review Packets", ""]
    for review in ordered:
        lines.extend(format_packet(review))
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def decision_rank(decision: str) -> int:
    return {"STRONG APPLY": 0, "APPLY": 1, "MODERATE STRETCH": 2, "EXTREME STRETCH": 3, "SKIP": 4}.get(decision, 5)


def format_packet(review: WorkflowReview) -> list[str]:
    lines = [
        f"## {review.company} — {review.title}",
        "",
        f"- Decision: **{review.workflow_decision}**",
        f"- Resume workflow: **{'YES' if review.should_generate_resume else 'NO'}**",
        f"- Resume base: {review.recommended_resume_base}",
        f"- Overqualified risk: **{review.overqualified_risk}**",
        f"- Resume positioning: {review.resume_positioning}",
        f"- Source fit: {review.source_fit_score} / source decision: {review.source_decision or 'unknown'}",
        f"- Location/work model: {review.location or 'unknown'} / {review.work_model or 'unknown'}",
        f"- Already applied: {'YES' if review.already_applied else 'NO'}"
        + (f" ({review.applied_date or review.applied_status})" if review.already_applied else ""),
        f"- URL: {review.url}",
        "",
        "Why it fits / direct matches:",
        *markdown_list(review.direct_matches),
        "",
        "Technical gaps — required:",
        *markdown_list(review.required_gaps),
        "",
        "Technical gaps — preferred/positioning:",
        *markdown_list(review.preferred_gaps),
        "",
        "Resolved gap answers:",
        *markdown_list([f"{gap}: {answer}" for gap, answer in review.resolved_gaps.items()]),
        "",
        "Hard blockers:",
        *markdown_list(review.hard_blockers),
        "",
        "Seniority/scope risks:",
        *markdown_list(review.seniority_scope_risks),
        "",
        "Interview positioning:",
        *markdown_list([review.interview_positioning_note] if review.interview_positioning_note else []),
        "",
        "Questions before resume:",
        *markdown_list(review.questions_for_candidate),
        "",
        f"Final action: {review.final_action}",
    ]
    return lines


def markdown_list(items: list[str]) -> list[str]:
    if not items:
        return ["- None"]
    return [f"- {escape_md(item)}" for item in items]


def main() -> None:
    parser = argparse.ArgumentParser(description="Strict workflow reviewer for discovered jobs.")
    parser.add_argument("--discovered-jobs", required=True, type=Path)
    parser.add_argument("--workflow-profile", default="candidate_workflow_profile.yml", type=Path)
    parser.add_argument("--job-overrides", default="job_overrides.yml", type=Path)
    parser.add_argument("--applied-jobs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--include-source-decisions", default="Apply,Maybe", help="Comma-separated source decisions to review. Default: Apply,Maybe.")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    run_date = dt.date.fromisoformat(args.date)
    workflow_profile = load_yaml(args.workflow_profile)
    job_overrides = load_job_overrides(args.job_overrides)
    tracker_jobs = load_tracker_jobs_snapshot(args.applied_jobs, expected_date=args.date)
    jobs = load_jobs(args.discovered_jobs)
    allowed_source_decisions = {normalize(item) for item in args.include_source_decisions.split(",") if item.strip()}

    current_reviewed_jobs = [
        job
        for job in jobs
        if normalize(job.get("decision", "")) in allowed_source_decisions
        and not is_excluded_company(job, workflow_profile)
        and not is_excluded_role_title(job, workflow_profile)
        and not is_skipped_by_job_override(job, job_overrides, as_of=run_date)
    ]
    carry_forward_jobs = [
        job
        for job in load_carry_forward_jobs(out_dir=args.out, run_date=args.date)
        if normalize(job.get("decision", "")) in allowed_source_decisions
        and not is_excluded_company(job, workflow_profile)
        and not is_excluded_role_title(job, workflow_profile)
        and not is_skipped_by_job_override(job, job_overrides, as_of=run_date)
    ]
    reviewed_jobs = dedupe_equivalent_postings(
        merge_current_and_carry_forward_jobs(current_reviewed_jobs, carry_forward_jobs)
    )
    reviews = [
        classify_review(job=job, workflow_profile=workflow_profile, tracker_jobs=tracker_jobs)
        for job in reviewed_jobs
    ]

    current_identities = {job_identity(job) for job in current_reviewed_jobs}
    new_carry_forward_count = sum(
        1 for job in carry_forward_jobs if job_identity(job) not in current_identities
    )
    json_path = write_json(
        reviews,
        args.out,
        args.date,
        source_jobs=reviewed_jobs,
        current_source_job_count=len(current_reviewed_jobs),
        carry_forward_source_job_count=new_carry_forward_count,
    )
    packet_path = write_packets(reviews, args.out, args.date)
    ready_path = write_resume_ready(reviews, args.out, args.date)
    html_path = write_workflow_review_html(
        review_path=json_path,
        output_path=args.out / f"{args.date}-workflow-review.html",
    )

    print(f"Wrote workflow review JSON: {json_path}")
    print(f"Wrote workflow review HTML: {html_path}")
    print(f"Wrote human review packets: {packet_path}")
    print(f"Wrote resume-ready jobs: {ready_path}")


if __name__ == "__main__":
    main()
