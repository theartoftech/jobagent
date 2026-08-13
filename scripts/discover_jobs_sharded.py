#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from scripts import discover_jobs
except ModuleNotFoundError:
    import discover_jobs  # type: ignore[no-redef]


DEFAULT_SHARD_COUNT = 10
DEFAULT_MAX_COMPANIES_PER_SHARD = 20
DEFAULT_PER_SHARD_TIMEOUT_SECONDS = 15 * 60
DEFAULT_MAX_AGE_DAYS = 60
DEFAULT_STARTUP_ROTATION_COUNT = 15
ROTATION_BUCKET_COUNT = 5
DEEP_SCAN_OVERDUE_DAYS = 7
DEFAULT_SENTINEL_WORKERS = 10
MAX_INITIAL_SENTINEL_JOBS_PER_BOARD = 3
MAX_SENTINEL_NEW_JOBS_PER_RUN = 50
MIN_STARTUP_WATCHLIST_SIZE = 50
MAX_STARTUP_WATCHLIST_SIZE = 75

COMPANY_ADAPTER_WEIGHTS: dict[str, int] = {
    "workday": 5,
    "smartrecruiters": 4,
    "greenhouse": 3,
    "icims": 3,
    "ashby": 3,
    "intuit": 3,
    "firstparty-listing": 3,
    "lever": 2,
    "workable": 2,
    "non-ats-root": 1,
}


@dataclass(frozen=True)
class CompanyShard:
    shard_id: int
    companies: list[dict[str, Any]]
    total_weight: int


@dataclass(frozen=True)
class CompanyRunStatus:
    company: str
    adapter_name: str
    status: str
    elapsed_seconds: float
    job_count: int
    registry_mapping_used: bool = False
    boards_queried: tuple[str, ...] = ()
    title_match_count: int = 0
    freshness_reject_count: int = 0
    adapter_error_count: int = 0
    root_only: bool = False


@dataclass(frozen=True)
class ShardRunResult:
    shard_id: int
    jobs: list[discover_jobs.JobPosting]
    errors: list[discover_jobs.DiscoveryError]
    company_statuses: list[CompanyRunStatus]
    timed_out: bool
    elapsed_seconds: float


@dataclass(frozen=True)
class SentinelTarget:
    board_key: str
    board_url: str
    adapter_name: str
    company: dict[str, Any]
    company_names: tuple[str, ...]


@dataclass(frozen=True)
class SentinelBoardStatus:
    board_key: str
    board_url: str
    adapter_name: str
    companies: tuple[str, ...]
    status: str
    current_job_count: int
    new_job_count: int
    error_count: int
    initial_baseline: bool


@dataclass(frozen=True)
class SentinelRunResult:
    new_jobs: list[discover_jobs.JobPosting]
    state: dict[str, Any]
    statuses: list[SentinelBoardStatus]
    errors: list[discover_jobs.DiscoveryError]


def load_startup_watchlist(path: Path) -> list[dict[str, Any]]:
    document = discover_jobs.load_yaml(path)
    if not isinstance(document, dict) or not isinstance(document.get("startups"), list):
        raise ValueError(f"{path} must contain a top-level 'startups' list.")
    raw_startups = document["startups"]
    if not MIN_STARTUP_WATCHLIST_SIZE <= len(raw_startups) <= MAX_STARTUP_WATCHLIST_SIZE:
        raise ValueError(
            f"Startup watchlist must contain between {MIN_STARTUP_WATCHLIST_SIZE} and "
            f"{MAX_STARTUP_WATCHLIST_SIZE} companies; found {len(raw_startups)} in {path}."
        )

    startups: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, item in enumerate(raw_startups, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Startup entry {index} in {path} must be a mapping.")
        name = item.get("name")
        careers_url = item.get("careers_url")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Startup entry {index} in {path} requires a non-empty name.")
        if not isinstance(careers_url, str) or not careers_url.startswith("https://"):
            raise ValueError(
                f"Startup {name!r} in {path} requires an HTTPS careers_url."
            )
        normalized_name = name.strip().casefold()
        if normalized_name in seen_names:
            raise ValueError(f"Duplicate startup name {name!r} in {path}.")
        seen_names.add(normalized_name)
        startups.append({**item, "name": name.strip(), "discovery_lane": "startup_rotation"})
    return startups


def select_startup_rotation(
    *,
    startups: list[dict[str, Any]],
    base_companies: list[dict[str, Any]],
    today: dt.date,
    count: int,
) -> list[dict[str, Any]]:
    if count <= 0:
        raise ValueError("startup-count must be positive.")
    base_names = {
        str(company.get("name") or "").strip().casefold()
        for company in base_companies
        if company.get("name")
    }
    eligible = [
        company
        for company in startups
        if str(company.get("name") or "").strip().casefold() not in base_names
    ]
    if not eligible:
        raise ValueError("Startup watchlist has no companies outside the base company list.")

    selected_count = min(count, len(eligible))
    cycle_days = math.ceil(len(eligible) / selected_count)
    cycle_day = today.toordinal() % cycle_days
    start = (cycle_day * selected_count) % len(eligible)
    return [eligible[(start + offset) % len(eligible)] for offset in range(selected_count)]


def company_board_urls(
    company: dict[str, Any],
    ats_registry: dict[str, list[str]] | None = None,
) -> tuple[str, ...]:
    registry_urls = (
        discover_jobs.ats_registry_urls_for_company(ats_registry, company)
        if ats_registry is not None
        else []
    )
    configured_urls = company.get("ats_urls", [])
    if not isinstance(configured_urls, list):
        configured_urls = []
    urls = [*registry_urls, *(str(url).strip() for url in configured_urls if str(url).strip())]
    return tuple(discover_jobs.dedupe(urls))


def sentinel_board_key(board_url: str) -> str:
    identity = discover_jobs.ats_identity_key(board_url)
    if identity:
        return f"{identity[0]}:{identity[1]}".casefold()
    return board_url.strip().rstrip("/").casefold()


def company_rotation_key(
    company: dict[str, Any],
    ats_registry: dict[str, list[str]],
) -> str:
    board_urls = company_board_urls(company, ats_registry)
    if board_urls:
        return sentinel_board_key(sorted(board_urls, key=str.casefold)[0])
    careers_url = str(company.get("careers_url") or "").strip()
    if careers_url:
        return careers_url.rstrip("/").casefold()
    return discover_jobs.normalize_company_key(str(company.get("name") or "Unknown Company"))


def company_rotation_bucket(
    company: dict[str, Any],
    ats_registry: dict[str, list[str]],
) -> int:
    digest = hashlib.sha256(company_rotation_key(company, ats_registry).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % ROTATION_BUCKET_COUNT


def build_rotation_bucket_map(
    companies: list[dict[str, Any]],
    ats_registry: dict[str, list[str]],
) -> dict[str, int]:
    keys = {company_rotation_key(company, ats_registry) for company in companies}
    ordered_keys = sorted(
        keys,
        key=lambda key: (hashlib.sha256(key.encode("utf-8")).hexdigest(), key),
    )
    return {key: index % ROTATION_BUCKET_COUNT for index, key in enumerate(ordered_keys)}


def parse_state_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def select_deep_scan_companies(
    *,
    companies: list[dict[str, Any]],
    today: dt.date,
    ats_registry: dict[str, list[str]],
    scan_state: dict[str, Any],
) -> list[dict[str, Any]]:
    if today.weekday() >= ROTATION_BUCKET_COUNT:
        return []
    state_companies = scan_state.get("companies", {}) if isinstance(scan_state, dict) else {}
    if not isinstance(state_companies, dict):
        state_companies = {}
    bucket_map = build_rotation_bucket_map(companies, ats_registry)
    selected: list[dict[str, Any]] = []
    for company in companies:
        company_name = str(company.get("name") or "Unknown Company")
        company_state = state_companies.get(discover_jobs.normalize_company_key(company_name), {})
        if not isinstance(company_state, dict):
            company_state = {}
        last_success = parse_state_date(company_state.get("last_successful_deep_scan"))
        overdue = last_success is not None and (today - last_success).days >= DEEP_SCAN_OVERDUE_DAYS
        forced = bool(company_state.get("retry_next_weekday"))
        daily = bool(company.get("daily_scan"))
        scheduled = bucket_map[company_rotation_key(company, ats_registry)] == today.weekday()
        if scheduled or overdue or forced or daily:
            selected.append(company)
    return selected


def sentinel_targets(
    companies: list[dict[str, Any]],
    ats_registry: dict[str, list[str]],
) -> list[SentinelTarget]:
    grouped: dict[str, dict[str, Any]] = {}
    for company in companies:
        board_urls = list(company_board_urls(company, ats_registry))
        careers_url = str(company.get("careers_url") or "").strip()
        if careers_url:
            try:
                careers_adapter = discover_jobs.adapter_for_url(careers_url)
            except ValueError:
                careers_adapter = None
            if (
                careers_adapter is not None
                and careers_adapter.name in discover_jobs.LIGHTWEIGHT_SENTINEL_ADAPTERS
            ):
                board_urls.append(careers_url)
        for board_url in discover_jobs.dedupe(board_urls):
            try:
                adapter = discover_jobs.adapter_for_url(board_url)
            except ValueError:
                continue
            if adapter is None or adapter.name not in discover_jobs.LIGHTWEIGHT_SENTINEL_ADAPTERS:
                continue
            key = sentinel_board_key(board_url)
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = {
                    "board_url": board_url,
                    "adapter_name": adapter.name,
                    "company": company,
                    "company_names": [str(company.get("name") or "Unknown Company")],
                }
            else:
                existing["company_names"].append(str(company.get("name") or "Unknown Company"))
    return [
        SentinelTarget(
            board_key=key,
            board_url=value["board_url"],
            adapter_name=value["adapter_name"],
            company=value["company"],
            company_names=tuple(discover_jobs.dedupe(value["company_names"])),
        )
        for key, value in sorted(grouped.items())
    ]


def sentinel_job_identity(job: discover_jobs.JobPosting) -> str:
    return str(job.job_id or job.url).strip()


def is_relevant_sentinel_job(job: discover_jobs.JobPosting, max_age_days: int) -> bool:
    return (
        bool(sentinel_job_identity(job))
        and job.fit_score >= 35
        and job.decision != "Skip"
        and not (job.posted_age_days is not None and job.posted_age_days > max_age_days)
    )


def run_ats_sentinel(
    *,
    companies: list[dict[str, Any]],
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    ats_registry: dict[str, list[str]],
    previous_state: dict[str, Any],
    max_workers: int = DEFAULT_SENTINEL_WORKERS,
) -> SentinelRunResult:
    targets = sentinel_targets(companies, ats_registry)
    previous_boards = previous_state.get("boards", {}) if isinstance(previous_state, dict) else {}
    if not isinstance(previous_boards, dict):
        previous_boards = {}

    def inspect(target: SentinelTarget) -> tuple[SentinelTarget, list[discover_jobs.JobPosting], list[discover_jobs.DiscoveryError]]:
        target_errors: list[discover_jobs.DiscoveryError] = []
        jobs = discover_jobs.discover_ats_sentinel(
            company=target.company,
            board_url=target.board_url,
            profile=profile,
            today=today,
            max_age_days=max_age_days,
            errors=target_errors,
        )
        return target, jobs, target_errors

    inspected: list[tuple[SentinelTarget, list[discover_jobs.JobPosting], list[discover_jobs.DiscoveryError]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(targets) or 1))) as executor:
        futures = [executor.submit(inspect, target) for target in targets]
        for future in concurrent.futures.as_completed(futures):
            inspected.append(future.result())
    inspected.sort(key=lambda item: item[0].board_key)

    state_boards: dict[str, Any] = {
        str(key): value
        for key, value in previous_boards.items()
        if isinstance(value, dict)
    }
    statuses: list[SentinelBoardStatus] = []
    errors: list[discover_jobs.DiscoveryError] = []
    new_jobs: list[discover_jobs.JobPosting] = []
    for target, jobs, target_errors in inspected:
        errors.extend(target_errors)
        prior = previous_boards.get(target.board_key, {})
        if not isinstance(prior, dict):
            prior = {}
        initial_baseline = target.board_key not in previous_boards
        prior_ids = {str(value) for value in prior.get("job_ids", [])}
        current_by_id = {
            sentinel_job_identity(job): job
            for job in jobs
            if sentinel_job_identity(job)
        }
        failed = bool(target_errors) and not current_by_id
        current_ids = sorted(current_by_id) if not failed else sorted(prior_ids)
        candidate_ids = [identity for identity in current_ids if identity not in prior_ids]
        candidates = [
            current_by_id[identity]
            for identity in candidate_ids
            if identity in current_by_id and is_relevant_sentinel_job(current_by_id[identity], max_age_days)
        ]
        candidates.sort(
            key=lambda job: (
                -job.fit_score,
                job.posted_age_days is None,
                job.posted_age_days if job.posted_age_days is not None else 9999,
                job.title.casefold(),
            )
        )
        if initial_baseline:
            candidates = [
                job
                for job in candidates
                if job.posted_age_days is not None and job.posted_age_days <= 7
            ][:MAX_INITIAL_SENTINEL_JOBS_PER_BOARD]
        available_slots = max(0, MAX_SENTINEL_NEW_JOBS_PER_RUN - len(new_jobs))
        candidates = candidates[:available_slots]
        for job in candidates:
            new_jobs.append(
                discover_jobs.hydrate_ats_sentinel_job(
                    board_url=target.board_url,
                    job=job,
                    profile=profile,
                    today=today,
                    max_age_days=max_age_days,
                    company=target.company,
                    errors=errors,
                )
            )
        state_boards[target.board_key] = {
            "board_url": target.board_url,
            "adapter": target.adapter_name,
            "companies": list(target.company_names),
            "last_checked": today.isoformat(),
            "status": "failed" if failed else "completed",
            "job_ids": current_ids,
        }
        statuses.append(
            SentinelBoardStatus(
                board_key=target.board_key,
                board_url=target.board_url,
                adapter_name=target.adapter_name,
                companies=target.company_names,
                status="failed" if failed else "completed",
                current_job_count=len(current_ids),
                new_job_count=len(candidates),
                error_count=len(target_errors),
                initial_baseline=initial_baseline,
            )
        )
    return SentinelRunResult(
        new_jobs=discover_jobs.dedupe_jobs(new_jobs),
        state={"version": 1, "updated_on": today.isoformat(), "boards": state_boards},
        statuses=statuses,
        errors=errors,
    )


def company_adapter_name(
    company: dict[str, Any],
    ats_registry: dict[str, list[str]] | None = None,
) -> str:
    board_urls = company_board_urls(company, ats_registry)
    if board_urls:
        board_adapter = discover_jobs.adapter_for_url(board_urls[0])
        if board_adapter is not None:
            return board_adapter.name
    careers_url = str(company.get("careers_url") or "")
    adapter = discover_jobs.adapter_for_url(careers_url)
    return adapter.name if adapter is not None else "non-ats-root"


def company_weight(company: dict[str, Any]) -> int:
    return COMPANY_ADAPTER_WEIGHTS.get(company_adapter_name(company), 1)


def plan_company_shards(
    *,
    companies: list[dict[str, Any]],
    shard_count: int,
    max_companies_per_shard: int,
) -> list[CompanyShard]:
    if shard_count <= 0:
        raise ValueError("shard_count must be positive.")
    if max_companies_per_shard <= 0:
        raise ValueError("max_companies_per_shard must be positive.")

    shard_companies: list[list[dict[str, Any]]] = [[] for _ in range(shard_count)]
    shard_weights = [0 for _ in range(shard_count)]
    weighted_companies = sorted(
        companies,
        key=lambda company: (-company_weight(company), str(company.get("name") or "").casefold()),
    )

    for company in weighted_companies:
        candidate_indices = [
            index for index, items in enumerate(shard_companies) if len(items) < max_companies_per_shard
        ]
        if not candidate_indices:
            raise ValueError("Not enough shard capacity for configured companies.")
        best_index = min(
            candidate_indices,
            key=lambda index: (shard_weights[index], len(shard_companies[index]), index),
        )
        shard_companies[best_index].append(company)
        shard_weights[best_index] += company_weight(company)

    return [
        CompanyShard(shard_id=index + 1, companies=items, total_weight=shard_weights[index])
        for index, items in enumerate(shard_companies)
    ]


def run_company_shard(
    *,
    shard: CompanyShard,
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    pause_seconds: float,
    ats_only: bool,
    per_shard_timeout_seconds: int,
    ats_registry: dict[str, list[str]] | None = None,
) -> ShardRunResult:
    started_at = time.monotonic()
    deadline = started_at + float(per_shard_timeout_seconds)
    jobs: list[discover_jobs.JobPosting] = []
    errors: list[discover_jobs.DiscoveryError] = []
    company_statuses: list[CompanyRunStatus] = []
    timed_out = False

    for index, company in enumerate(shard.companies):
        company_name = str(company.get("name") or "Unknown Company")
        boards_queried = company_board_urls(company, ats_registry)
        registry_urls = (
            discover_jobs.ats_registry_urls_for_company(ats_registry, company)
            if ats_registry is not None
            else []
        )
        adapter_name = company_adapter_name(company, ats_registry)
        now = time.monotonic()
        if now >= deadline:
            timed_out = True
            remaining_companies = shard.companies[index:]
            for remaining_company in remaining_companies:
                company_statuses.append(
                    CompanyRunStatus(
                        company=str(remaining_company.get("name") or "Unknown Company"),
                        adapter_name=company_adapter_name(remaining_company, ats_registry),
                        status="timed_out_before_start",
                        elapsed_seconds=0.0,
                        job_count=0,
                    )
                )
            break

        company_started_at = now
        error_count_before = len(errors)
        company_jobs = discover_jobs.discover_company(
            company,
            profile,
            today,
            max_age_days,
            errors,
            pause_seconds,
            ats_only,
            ats_registry=ats_registry,
        )
        elapsed_seconds = time.monotonic() - company_started_at
        jobs.extend(company_jobs)
        company_statuses.append(
            CompanyRunStatus(
                company=company_name,
                adapter_name=adapter_name,
                status="completed",
                elapsed_seconds=round(elapsed_seconds, 2),
                job_count=len(company_jobs),
                registry_mapping_used=bool(registry_urls),
                boards_queried=boards_queried,
                title_match_count=sum(
                    1
                    for job in company_jobs
                    if any(reason == "target title match" for reason in job.fit_reasons)
                ),
                freshness_reject_count=sum(
                    1
                    for job in company_jobs
                    if any(
                        "older than" in risk.casefold()
                        or "freshness verification" in risk.casefold()
                        for risk in job.risk_flags
                    )
                ),
                adapter_error_count=len(errors) - error_count_before,
                root_only=not boards_queried and adapter_name == "non-ats-root",
            )
        )

    total_elapsed = time.monotonic() - started_at
    return ShardRunResult(
        shard_id=shard.shard_id,
        jobs=jobs,
        errors=errors,
        company_statuses=company_statuses,
        timed_out=timed_out,
        elapsed_seconds=round(total_elapsed, 2),
    )


def merge_shard_results(results: list[ShardRunResult]) -> dict[str, Any]:
    jobs: list[discover_jobs.JobPosting] = []
    errors: list[discover_jobs.DiscoveryError] = []
    timed_out_companies: list[CompanyRunStatus] = []
    company_statuses: list[CompanyRunStatus] = []

    for result in results:
        jobs.extend(result.jobs)
        errors.extend(result.errors)
        company_statuses.extend(result.company_statuses)
        timed_out_companies.extend(
            status
            for status in result.company_statuses
            if status.status.startswith("timed_out")
        )

    deduped_jobs = discover_jobs.dedupe_jobs(jobs)
    return {
        "jobs": deduped_jobs,
        "errors": errors,
        "timed_out_companies": timed_out_companies,
        "company_statuses": company_statuses,
    }


def load_state_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON state file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"State file {path} must contain a JSON object.")
    return value


def write_state_file(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary_path.replace(path)


def update_deep_scan_state(
    *,
    previous_state: dict[str, Any],
    base_companies: list[dict[str, Any]],
    selected_companies: list[dict[str, Any]],
    company_statuses: list[CompanyRunStatus],
    today: dt.date,
    ats_registry: dict[str, list[str]],
) -> dict[str, Any]:
    previous_companies = previous_state.get("companies", {}) if isinstance(previous_state, dict) else {}
    if not isinstance(previous_companies, dict):
        previous_companies = {}
    selected_names = {
        discover_jobs.normalize_company_key(str(company.get("name") or "Unknown Company"))
        for company in selected_companies
    }
    statuses = {
        discover_jobs.normalize_company_key(status.company): status
        for status in company_statuses
    }
    next_companies: dict[str, Any] = {}
    bucket_map = build_rotation_bucket_map(base_companies, ats_registry)
    for company in base_companies:
        company_name = str(company.get("name") or "Unknown Company")
        key = discover_jobs.normalize_company_key(company_name)
        existing = previous_companies.get(key, {})
        entry = dict(existing) if isinstance(existing, dict) else {}
        entry.update(
            {
                "company": company_name,
                "rotation_bucket": bucket_map[company_rotation_key(company, ats_registry)],
            }
        )
        if key in selected_names:
            status = statuses.get(key)
            successful = bool(
                status is not None
                and status.status == "completed"
                and status.adapter_error_count == 0
            )
            entry["last_attempted_deep_scan"] = today.isoformat()
            entry["retry_next_weekday"] = not successful
            if successful:
                entry["last_successful_deep_scan"] = today.isoformat()
        next_companies[key] = entry
    return {"version": 1, "updated_on": today.isoformat(), "companies": next_companies}


def write_sentinel_artifacts(
    *,
    out_dir: Path,
    today: dt.date,
    result: SentinelRunResult,
) -> None:
    payload = {
        "generated_on": today.isoformat(),
        "new_job_count": len(result.new_jobs),
        "statuses": [asdict(status) for status in result.statuses],
        "new_jobs": [asdict(job) for job in result.new_jobs],
        "errors": [asdict(error) for error in result.errors],
    }
    discover_jobs.write_json(out_dir / f"{today.isoformat()}-ats-sentinel.json", payload)


def collect_external_source_jobs(
    *,
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    errors: list[discover_jobs.DiscoveryError],
) -> list[discover_jobs.JobPosting]:
    return discover_jobs.discover_usajobs(
        profile=profile,
        today=today,
        max_age_days=max_age_days,
        errors=errors,
    )


def write_shard_artifacts(
    *,
    out_dir: Path,
    today: dt.date,
    shards: list[CompanyShard],
    results: list[ShardRunResult],
    merged: dict[str, Any],
    max_age_days: int,
    startup_rotation: list[dict[str, Any]] | None = None,
    startup_watchlist_size: int = 0,
    rotation_enabled: bool = False,
    base_company_count: int = 0,
    deep_scan_companies: list[dict[str, Any]] | None = None,
    sentinel_result: SentinelRunResult | None = None,
    ats_registry: dict[str, list[str]] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / f"{today.isoformat()}-shard-manifest.json"
    results_path = out_dir / f"{today.isoformat()}-shard-results.json"
    summary_path = out_dir / f"{today.isoformat()}-shard-summary.md"
    discovered_jobs_path = out_dir / f"{today.isoformat()}-discovered-jobs.json"
    ats_summary_path = out_dir / f"{today.isoformat()}-ats-discovery.md"

    manifest_payload = {
        "generated_on": today.isoformat(),
        "startup_rotation": {
            "watchlist_size": startup_watchlist_size,
            "selected_count": len(startup_rotation or []),
            "companies": [company["name"] for company in startup_rotation or []],
        },
        "deep_scan_rotation": {
            "enabled": rotation_enabled,
            "weekday_bucket": today.weekday() if today.weekday() < ROTATION_BUCKET_COUNT else None,
            "base_company_count": base_company_count,
            "selected_count": len(deep_scan_companies or []),
            "deferred_count": max(0, base_company_count - len(deep_scan_companies or [])),
            "companies": [company["name"] for company in deep_scan_companies or []],
        },
        "ats_sentinel": {
            "boards_checked": len(sentinel_result.statuses) if sentinel_result else 0,
            "new_jobs": len(sentinel_result.new_jobs) if sentinel_result else 0,
            "failed_boards": sum(
                1 for status in sentinel_result.statuses if status.status == "failed"
            ) if sentinel_result else 0,
        },
        "shards": [
            {
                "shard_id": shard.shard_id,
                "total_weight": shard.total_weight,
                "companies": [company["name"] for company in shard.companies],
            }
            for shard in shards
        ],
    }
    manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True), encoding="utf-8")

    results_payload = {
        "generated_on": today.isoformat(),
        "results": [
            {
                "shard_id": result.shard_id,
                "timed_out": result.timed_out,
                "elapsed_seconds": result.elapsed_seconds,
                "company_statuses": [asdict(status) for status in result.company_statuses],
                "error_count": len(result.errors),
                "job_count": len(result.jobs),
            }
            for result in results
        ],
    }
    results_path.write_text(json.dumps(results_payload, indent=2, sort_keys=True), encoding="utf-8")

    jobs = merged["jobs"]
    errors = merged["errors"]
    discover_jobs.write_json(
        discovered_jobs_path,
        discover_jobs.build_discovered_jobs_payload(
            jobs=jobs,
            errors=errors,
            generated_on=today.isoformat(),
            max_age_days=max_age_days,
        ),
    )
    discover_jobs.write_markdown_summary(ats_summary_path, jobs, errors, today)

    lines: list[str] = []
    lines.append(f"# Sharded Discovery Summary - {today.isoformat()}")
    lines.append("")
    lines.append("## Daily ATS Sentinel")
    lines.append("")
    if sentinel_result is None:
        lines.append("- Not run")
    else:
        lines.append(f"- Unique structured ATS boards checked: {len(sentinel_result.statuses)}")
        lines.append(f"- Newly observed relevant jobs: {len(sentinel_result.new_jobs)}")
        lines.append(
            f"- Failed sentinel boards: {sum(1 for status in sentinel_result.statuses if status.status == 'failed')}"
        )
    lines.append("")
    lines.append("## Deep Scan Rotation")
    lines.append("")
    lines.append(f"- Rotation enabled: {'Yes' if rotation_enabled else 'No'}")
    lines.append(f"- Established companies: {base_company_count}")
    lines.append(f"- Deep-scanned today: {len(deep_scan_companies or [])}")
    lines.append(f"- Deferred to another weekday: {max(0, base_company_count - len(deep_scan_companies or []))}")
    for company in deep_scan_companies or []:
        lines.append(f"- {company['name']}")
    lines.append("")
    lines.append("## Startup Rotation")
    lines.append("")
    lines.append(f"- Watchlist companies: {startup_watchlist_size}")
    lines.append(f"- Selected today: {len(startup_rotation or [])}")
    for company in startup_rotation or []:
        lines.append(f"- {company['name']} - {company['careers_url']}")
    lines.append("")
    lines.append("## Shards")
    lines.append("")
    lines.append("| Shard | Weight | Companies | Timed Out | Elapsed (s) |")
    lines.append("|---:|---:|---:|---|---:|")
    result_by_id = {result.shard_id: result for result in results}
    for shard in shards:
        result = result_by_id.get(shard.shard_id)
        lines.append(
            f"| {shard.shard_id} | {shard.total_weight} | {len(shard.companies)} | "
            f"{'Yes' if result and result.timed_out else 'No'} | {result.elapsed_seconds if result else 0} |"
        )

    lines.append("")
    lines.append("## Timed Out Before Start")
    lines.append("")
    timed_out_companies: list[CompanyRunStatus] = merged["timed_out_companies"]
    if not timed_out_companies:
        lines.append("- None")
    else:
        for status in timed_out_companies:
            lines.append(f"- {status.company} ({status.adapter_name})")

    lines.append("")
    lines.append("## Company Miss Diagnostics")
    lines.append("")
    lines.append(
        "| Company | Adapter | Registry | Boards Queried | Jobs | Title Matches | "
        "Freshness Rejects | Errors | Root Only | Elapsed (s) | Miss Reason |"
    )
    lines.append("|---|---|---|---|---:|---:|---:|---:|---|---:|---|")
    for status in sorted(merged["company_statuses"], key=lambda item: (item.job_count != 0, item.company)):
        boards = "<br>".join(status.boards_queried) or "-"
        lines.append(
            f"| {status.company} | {status.adapter_name} | "
            f"{'Yes' if status.registry_mapping_used else 'No'} | {boards} | "
            f"{status.job_count} | {status.title_match_count} | {status.freshness_reject_count} | "
            f"{status.adapter_error_count} | {'Yes' if status.root_only else 'No'} | "
            f"{status.elapsed_seconds} | {company_miss_reason(status)} |"
        )

    summary_path.write_text("\n".join(lines), encoding="utf-8")


def company_miss_reason(status: CompanyRunStatus) -> str:
    if status.status.startswith("timed_out"):
        return "Shard timeout prevented this company from running."
    if status.job_count > 0:
        return "-"
    if status.root_only or status.adapter_name == "non-ats-root":
        return "no_registry_mapping: no structured ATS adapter resolved from configured root URL."
    return f"{status.adapter_name} adapter completed but returned zero jobs."


def main() -> None:
    parser = argparse.ArgumentParser(description="Run sharded company discovery with per-shard timeout budgets.")
    parser.add_argument("--companies", default="companies.yml")
    parser.add_argument("--profile", default="candidate_profile.yml")
    parser.add_argument("--out", default="outputs")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--ats-registry", default="ats_registry.yml")
    parser.add_argument("--manual-job-urls", default="manual_job_urls.yml")
    parser.add_argument("--job-overrides", default="job_overrides.yml")
    parser.add_argument("--startup-watchlist", default="startup_watchlist.yml")
    parser.add_argument("--startup-count", type=int, default=DEFAULT_STARTUP_ROTATION_COUNT)
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    parser.add_argument("--ats-only", action="store_true")
    parser.add_argument("--shard-count", type=int, default=DEFAULT_SHARD_COUNT)
    parser.add_argument("--max-companies-per-shard", type=int, default=DEFAULT_MAX_COMPANIES_PER_SHARD)
    parser.add_argument("--per-shard-timeout-seconds", type=int, default=DEFAULT_PER_SHARD_TIMEOUT_SECONDS)
    parser.add_argument(
        "--rotation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Deep-scan one stable weekday bucket while retaining daily ATS sentinel checks.",
    )
    parser.add_argument("--sentinel-workers", type=int, default=DEFAULT_SENTINEL_WORKERS)
    parser.add_argument("--sentinel-state")
    parser.add_argument("--deep-scan-state")
    args = parser.parse_args()

    profile = discover_jobs.profile_doc_to_profile(discover_jobs.load_yaml(Path(args.profile)))
    base_companies = [
        company
        for company in discover_jobs.normalize_company_entries(discover_jobs.load_yaml(Path(args.companies)))
        if not discover_jobs.is_excluded_company(str(company.get("name", "")), profile)
    ]
    startup_watchlist = [
        company
        for company in load_startup_watchlist(Path(args.startup_watchlist))
        if not discover_jobs.is_excluded_company(str(company.get("name", "")), profile)
    ]
    ats_registry = discover_jobs.load_ats_registry(Path(args.ats_registry))
    job_overrides = discover_jobs.load_job_overrides(Path(args.job_overrides))
    today = dt.date.today()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sentinel_state_path = Path(args.sentinel_state) if args.sentinel_state else out_dir / "ats-sentinel-state.json"
    deep_scan_state_path = Path(args.deep_scan_state) if args.deep_scan_state else out_dir / "company-deep-scan-state.json"
    previous_sentinel_state = load_state_file(sentinel_state_path)
    previous_deep_scan_state = load_state_file(deep_scan_state_path)
    startup_rotation = select_startup_rotation(
        startups=startup_watchlist,
        base_companies=base_companies,
        today=today,
        count=args.startup_count,
    )
    deep_scan_companies = (
        select_deep_scan_companies(
            companies=base_companies,
            today=today,
            ats_registry=ats_registry,
            scan_state=previous_deep_scan_state,
        )
        if args.rotation
        else list(base_companies)
    )
    companies = [*deep_scan_companies, *startup_rotation]
    sentinel_result = run_ats_sentinel(
        companies=[*base_companies, *startup_rotation],
        profile=profile,
        today=today,
        max_age_days=args.max_age_days,
        ats_registry=ats_registry,
        previous_state=previous_sentinel_state,
        max_workers=args.sentinel_workers,
    )
    write_state_file(sentinel_state_path, sentinel_result.state)
    write_sentinel_artifacts(out_dir=out_dir, today=today, result=sentinel_result)

    shards = plan_company_shards(
        companies=companies,
        shard_count=args.shard_count,
        max_companies_per_shard=args.max_companies_per_shard,
    )
    active_shards = [shard for shard in shards if shard.companies]

    results: list[ShardRunResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_shards) or 1) as executor:
        future_map = {
            executor.submit(
                run_company_shard,
                shard=shard,
                profile=profile,
                today=today,
                max_age_days=args.max_age_days,
                pause_seconds=args.pause_seconds,
                ats_only=args.ats_only,
                per_shard_timeout_seconds=args.per_shard_timeout_seconds,
                ats_registry=ats_registry,
            ): shard
            for shard in active_shards
        }
        for future in concurrent.futures.as_completed(future_map):
            results.append(future.result())

    results.sort(key=lambda item: item.shard_id)
    merged = merge_shard_results(results)
    merged["errors"].extend(sentinel_result.errors)
    if sentinel_result.new_jobs:
        merged["jobs"] = discover_jobs.dedupe_jobs([*merged["jobs"], *sentinel_result.new_jobs])
    manual_jobs = discover_jobs.discover_manual_job_urls(
        manual_path=Path(args.manual_job_urls),
        companies=[*base_companies, *startup_rotation],
        profile=profile,
        today=today,
        max_age_days=args.max_age_days,
        errors=merged["errors"],
    )
    if manual_jobs:
        merged["jobs"] = discover_jobs.dedupe_jobs([*merged["jobs"], *manual_jobs])
    external_jobs = collect_external_source_jobs(
        profile=profile,
        today=today,
        max_age_days=args.max_age_days,
        errors=merged["errors"],
    )
    if external_jobs:
        merged["jobs"] = discover_jobs.dedupe_jobs([*merged["jobs"], *external_jobs])
    merged["jobs"] = [
        job for job in merged["jobs"]
        if not discover_jobs.is_excluded_company(job.company, profile)
    ]
    discover_jobs.apply_job_overrides(merged["jobs"], job_overrides)
    deep_scan_state = update_deep_scan_state(
        previous_state=previous_deep_scan_state,
        base_companies=base_companies,
        selected_companies=deep_scan_companies,
        company_statuses=merged["company_statuses"],
        today=today,
        ats_registry=ats_registry,
    )
    write_state_file(deep_scan_state_path, deep_scan_state)
    write_shard_artifacts(
        out_dir=out_dir,
        today=today,
        shards=active_shards,
        results=results,
        merged=merged,
        max_age_days=args.max_age_days,
        startup_rotation=startup_rotation,
        startup_watchlist_size=len(startup_watchlist),
        rotation_enabled=args.rotation,
        base_company_count=len(base_companies),
        deep_scan_companies=deep_scan_companies,
        sentinel_result=sentinel_result,
        ats_registry=ats_registry,
    )

    jobs: list[discover_jobs.JobPosting] = merged["jobs"]
    timed_out_companies: list[CompanyRunStatus] = merged["timed_out_companies"]
    print(f"Sharded discovery complete: {len(active_shards)} shards")
    print(f"Official jobs discovered: {len(jobs)}")
    print(
        f"Daily ATS sentinel: {len(sentinel_result.statuses)} boards, "
        f"{len(sentinel_result.new_jobs)} new relevant jobs"
    )
    print(
        f"Deep-scan rotation: {len(deep_scan_companies)} of {len(base_companies)} "
        "established companies selected"
    )
    print(
        f"Startup rotation: {len(startup_rotation)} selected from "
        f"{len(startup_watchlist)} watchlist companies"
    )
    print(f"Timed-out companies not started: {len(timed_out_companies)}")
    print(f"Shard summary: {out_dir / f'{today.isoformat()}-shard-summary.md'}")


if __name__ == "__main__":
    main()
