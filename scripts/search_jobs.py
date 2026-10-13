#!/usr/bin/env python3
"""
Daily job-search plan generator for Codex.

Purpose:
- Read companies.yml, candidate_profile.yml, and search_rules.md.
- Generate a dated Markdown search plan for Codex.
- Generate a dated report template for Codex to fill after live searching.

Important:
This script does NOT scrape job sites.
Codex should perform the live web search using the generated plan.

Usage:
  python scripts/search_jobs.py \
    --companies companies.yml \
    --profile candidate_profile.yml \
    --rules search_rules.md \
    --out outputs
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

try:
    from scripts.mark_applied_jobs import DEFAULT_JOBTRACKER_SSH_HOST
except ModuleNotFoundError:
    from mark_applied_jobs import DEFAULT_JOBTRACKER_SSH_HOST

try:
    from scripts.job_override_dates import filter_companies_by_overrides
except ModuleNotFoundError:
    from job_override_dates import filter_companies_by_overrides


MAX_DETAILED_COMPANY_SECTIONS_IN_PLAN = 25
MAX_SUGGESTED_SEARCHES_PER_COMPANY_IN_PLAN = 3
HUMAN_SEARCH_COMPANIES_PER_RUN = 5
HUMAN_SEARCH_STATE_FILE = "human-search-rotation.json"


def load_yaml(path: Path) -> dict[str, Any] | list[Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")

    raw_text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(raw_text)
    else:
        data = load_yaml_via_ruby(path)

    if isinstance(data, str):
        data = [line.strip() for line in raw_text.splitlines() if line.strip()]

    if not isinstance(data, (dict, list)):
        raise ValueError(f"Expected YAML mapping or list in {path}")

    return data


def load_yaml_via_ruby(path: Path) -> dict[str, Any] | list[Any]:
    ruby = shutil.which("ruby")

    if not ruby:
        raise ModuleNotFoundError(
            "PyYAML is not installed and Ruby is not available for YAML fallback parsing."
        )

    command = [
        ruby,
        "-ryaml",
        "-rjson",
        "-e",
        'print JSON.generate(YAML.load_file(ARGV[0]))',
        str(path),
    ]

    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )

    return json.loads(result.stdout)


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")

    return path.read_text(encoding="utf-8").strip()


def normalize_company_entries(companies_doc: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    companies = companies_doc.get("companies") if isinstance(companies_doc, dict) else companies_doc

    if not isinstance(companies, list):
        raise ValueError("companies.yml must contain a top-level 'companies' list.")

    normalized: list[dict[str, Any]] = []

    for item in companies:
        if isinstance(item, str):
            normalized.append({"name": item})
        elif isinstance(item, dict) and item.get("name"):
            normalized.append(item)
        else:
            raise ValueError(f"Invalid company entry in companies.yml: {item!r}")

    return normalized


def get_profile_section(profile_doc: dict[str, Any]) -> dict[str, Any]:
    profile = profile_doc.get("target_profile")

    if not isinstance(profile, dict):
        raise ValueError("candidate_profile.yml must contain top-level 'target_profile' mapping.")

    return profile


def domain_from_url(url: str) -> str:
    cleaned = (
        url.replace("https://", "")
        .replace("http://", "")
        .split("/")[0]
        .strip()
    )
    return cleaned


def build_title_queries(company: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    company_name = company["name"]
    careers_url = company.get("careers_url", "")
    careers_domain = domain_from_url(careers_url) if careers_url else ""

    title_targets = profile.get("title_targets", [])
    strong_keywords = profile.get("strong_keywords", [])

    if not isinstance(title_targets, list):
        raise ValueError("target_profile.title_targets must be a list.")

    if not isinstance(strong_keywords, list):
        raise ValueError("target_profile.strong_keywords must be a list.")

    priority_titles = [
        title for title in title_targets
        if any(
            token in title.lower()
            for token in [
                "principal",
                "staff",
                "lead",
                "senior",
                "backend",
                "java",
                "platform",
                "integration",
                "database",
                "solution",
                "architect",
                "consultant",
                "consulting",
                "professional services",
                "implementation",
                "delivery",
                "customer",
                "account",
                "cpq",
                "software engineer iv",
                "forward deployed",
            ]
        )
    ]

    if not priority_titles:
        priority_titles = title_targets[:8]

    keyword_query = " ".join(
        [
            '"Java"',
            '"Spring Boot"',
            '"REST"',
            '"SQL"',
            '"backend"',
            '"enterprise"',
            '"API"',
            '"integration"',
            '"technical consulting"',
        ]
    )

    queries: list[str] = []

    official_search_domains = company.get("official_search_domains", [])
    if isinstance(official_search_domains, list):
        for domain in official_search_domains:
            cleaned_domain = (
                str(domain)
                .strip()
                .replace("https://", "")
                .replace("http://", "")
                .strip("/")
            )
            if cleaned_domain:
                queries.append(
                    f'site:{cleaned_domain} "Java" "Spring Boot" "backend" "remote"'
                )

    for title in priority_titles[:6]:
        queries.append(
            f'{company_name} careers "{title}" {keyword_query} posted'
        )

    if careers_domain:
        queries.extend(
            [
                f'site:{careers_domain} "Software Engineer" "{company_name}" "Java"',
                f'site:{careers_domain} "Principal Software Engineer" OR "Staff Software Engineer"',
                f'site:{careers_domain} "Backend" "Java" "SQL"',
                f'site:{careers_domain} "Technical Consultant" OR "Solution Architect"',
                f'site:{careers_domain} "Implementation Architect" OR "Professional Services Engineer"',
                f'site:{careers_domain} "Integration Consultant" OR "Delivery Architect"',
                f'site:{careers_domain} "Forward Deployed Engineer" OR "Forward Deployed Architect"',
                f'site:{careers_domain} "posted" "Software Engineer"',
            ]
        )

    return dedupe_preserve_order(queries)[:MAX_SUGGESTED_SEARCHES_PER_COMPANY_IN_PLAN]


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []

    for item in items:
        if item not in seen:
            output.append(item)
            seen.add(item)

    return output


def load_human_search_state(out_dir: Path) -> dict[str, Any]:
    path = out_dir / HUMAN_SEARCH_STATE_FILE
    if not path.exists():
        return {"suggested_company_names": []}

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected object in {path}")
    suggested = data.get("suggested_company_names", [])
    if not isinstance(suggested, list):
        raise ValueError(f"Expected suggested_company_names list in {path}")
    return {"suggested_company_names": [str(item) for item in suggested]}


def write_human_search_state(out_dir: Path, state: dict[str, Any]) -> None:
    path = out_dir / HUMAN_SEARCH_STATE_FILE
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def select_human_search_companies(
    companies: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    count: int = HUMAN_SEARCH_COMPANIES_PER_RUN,
) -> list[dict[str, Any]]:
    suggested_names = {
        str(name)
        for name in state.get("suggested_company_names", [])
        if str(name).strip()
    }
    eligible = [company for company in companies if company.get("name")]
    selected = [
        company
        for company in eligible
        if str(company["name"]) not in suggested_names
    ][:count]
    selected_names = {str(company["name"]) for company in selected}
    for company in eligible:
        if len(selected) >= count:
            break
        name = str(company["name"])
        if name in selected_names:
            continue
        selected.append(company)
        selected_names.add(name)
    return selected


def update_human_search_state(
    state: dict[str, Any],
    selected_companies: list[dict[str, Any]],
    all_companies: list[dict[str, Any]],
) -> dict[str, Any]:
    all_names = [str(company["name"]) for company in all_companies if company.get("name")]
    all_name_set = set(all_names)
    current_names = [
        str(name)
        for name in state.get("suggested_company_names", [])
        if str(name) in all_name_set
    ]

    selected_names = [
        str(company.get("name", "")).strip()
        for company in selected_companies
        if str(company.get("name", "")).strip()
    ]
    unsearched_names = [name for name in all_names if name not in set(current_names)]

    if not unsearched_names:
        current_names = []
    elif len(selected_names) > len(unsearched_names):
        current_names = selected_names[len(unsearched_names):]
        return {"suggested_company_names": current_names}

    seen = set(current_names)
    for name in selected_names:
        if name and name not in seen:
            current_names.append(name)
            seen.add(name)

    return {"suggested_company_names": current_names}


def format_human_search_companies(companies: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for index, company in enumerate(companies, start=1):
        name = str(company.get("name", "")).strip()
        careers_url = str(
            company.get("human_search_url") or company.get("careers_url", "")
        ).strip()
        if careers_url:
            lines.append(f"{index}. {name} - {careers_url}")
        else:
            lines.append(f"{index}. {name}")
    return lines


def write_human_search_rotation(
    *,
    today: str,
    companies: list[dict[str, Any]],
    out_dir: Path,
    count: int = HUMAN_SEARCH_COMPANIES_PER_RUN,
) -> list[dict[str, Any]]:
    state = load_human_search_state(out_dir)
    selected = select_human_search_companies(companies, state, count=count)
    updated_state = update_human_search_state(state, selected, companies)
    write_human_search_state(out_dir, updated_state)

    lines = [
        f"## Human Search Companies - {today}",
        "",
        "Manually search these company career sites and compare any roles found against the agent output.",
        "",
        *format_human_search_companies(selected),
        "",
    ]
    (out_dir / f"{today}-human-search-companies.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return selected


def format_profile_summary(profile: dict[str, Any]) -> str:
    strong_keywords = profile.get("strong_keywords", [])
    acceptable_secondary = profile.get("acceptable_secondary_keywords", [])
    exclusions = profile.get("exclude_if_primary_focus", [])
    locations = profile.get("locations", {})

    lines: list[str] = []

    lines.append("### Strong-match keywords")
    for keyword in strong_keywords:
        lines.append(f"- {keyword}")

    if acceptable_secondary:
        lines.append("")
        lines.append("### Acceptable secondary keywords")
        for keyword in acceptable_secondary:
            lines.append(f"- {keyword}")

    lines.append("")
    lines.append("### Exclude if primary focus")
    for item in exclusions:
        lines.append(f"- {item}")

    if isinstance(locations, dict):
        lines.append("")
        lines.append("### Location rules")

        for bucket in ["preferred", "acceptable", "reject"]:
            values = locations.get(bucket, [])
            if values:
                lines.append(f"- {bucket.capitalize()}: {', '.join(values)}")

    return "\n".join(lines)


def write_search_plan(
    *,
    today: str,
    companies: list[dict[str, Any]],
    profile: dict[str, Any],
    rules_text: str,
    out_dir: Path,
    human_search_companies: list[dict[str, Any]] | None = None,
) -> Path:
    output_path = out_dir / f"{today}-search-plan.md"

    lines: list[str] = []

    lines.append(f"# Daily Job Search Plan - {today}")
    lines.append("")
    lines.append("## Mission")
    lines.append("")
    lines.append(
        "Find non-stretch software engineering roles the candidate should consider applying to."
    )
    lines.append("")
    lines.append("## Search instructions for Codex")
    lines.append("")
    lines.append("1. Run sharded ATS/company discovery first so the official-site scan finishes within the runtime budget.")
    lines.append("2. Search each company's official careers site first.")
    lines.append("3. Use public job boards as discovery sources when the official site is hard to search.")
    lines.append("4. Use the suggested web searches to discover exact titles, requisition IDs, alternate posting URLs, and official postings.")
    lines.append("5. Include only jobs posted within the last 60 days in Apply/Maybe; route live official evergreen postings older than 60 days to Needs Freshness Verification.")
    lines.append(
        "6. For the primary broad inspection queue, order jobs by fit first, then 7-day / 21-day / 60-day freshness."
    )
    lines.append("7. Do not fabricate posting dates.")
    lines.append(
        "8. If no posting date is visible on an otherwise live official job page, keep the date field honest but treat the role as freshest for scoring and ranking."
    )
    lines.append("9. Prefer direct company career-site URLs over aggregator URLs.")
    lines.append("10. Reject stale indexed results that lead to 404, delisted, expired, or unavailable job pages.")
    lines.append("11. Do not recommend a public job-board result if the official company page is delisted, expired, unavailable, or materially different.")
    lines.append("12. Apply the candidate's no-stretch rule.")
    lines.append("")
    lines.append("Run this command before reading ATS discovery outputs:")
    lines.append("")
    lines.append(
        f"scripts/jobagent_python.sh scripts/discover_jobs_sharded.py --companies companies.yml --profile candidate_profile.yml "
        f"--out {out_dir} --max-age-days 60 --ats-registry ats_registry.yml "
        f"--manual-job-urls manual_job_urls.yml --startup-watchlist startup_watchlist.yml "
        f"--startup-count 15 --rotation --pause-seconds 0 --shard-count 10 "
        f"--max-companies-per-shard 20 --per-shard-timeout-seconds 900"
    )
    lines.append("")
    lines.append(
        "Use `ats_registry.yml` as the durable map of known company ATS boards before scraping root career pages."
    )
    lines.append(
        "Use `manual_job_urls.yml` as first-class candidate input for official URLs the candidate found manually."
    )
    lines.append(
        "Treat browser-reviewed job findings from LinkedIn, Indeed, or any other site as manual candidate intake: "
        "resolve each finding to its live official company/ATS URL, add qualifying or unresolved plausible roles to "
        "`manual_job_urls.yml`, and include them in the current dated discovery and strict workflow reports."
    )
    lines.append(
        "Use the authenticated AI Apply MCP as a bounded read-only discovery and application-status source. "
        "The candidate profile and search rules in this repository remain authoritative; do not inherit AI Apply's saved exclusions. "
        "Resolve every candidate to a live official company or ATS posting before Apply or Maybe, treat an AI Apply internal job ID "
        "as source-scoped rather than an employer requisition ID, and never call `queue_job_for_review` or `submit_application`."
    )
    lines.append(
        "USAJOBS federal-job discovery is included when `USAJOBS_USER_AGENT` and "
        "`USAJOBS_AUTHORIZATION_KEY` are present in the local environment; if either "
        "is missing, the run should skip USAJOBS with a diagnostic rather than fail."
    )
    lines.append("")
    lines.append("Then build the deterministic broad inspection queue from the discovered jobs:")
    lines.append("")
    lines.append(
        f"scripts/jobagent_python.sh scripts/build_job_inspection_queue.py --discovered-jobs {out_dir / f'{today}-discovered-jobs.json'} "
        f"--out {out_dir} --date {today}"
    )
    lines.append("")
    lines.append("Then reconcile the daily report with jobtracker and write the required applied-jobs snapshot:")
    lines.append("")
    lines.append(
        f"scripts/jobagent_python.sh scripts/mark_applied_jobs.py --daily-report {out_dir / f'{today}-daily-job-search.md'} "
        f"--ssh-host {DEFAULT_JOBTRACKER_SSH_HOST} --applied-jobs-out {out_dir / f'{today}-applied-jobs.json'}"
    )
    lines.append("")
    lines.append("Then run the strict resume workflow reviewer before any Word document work:")
    lines.append("")
    lines.append(
        f"scripts/jobagent_python.sh scripts/review_job_fit.py --discovered-jobs {out_dir / f'{today}-discovered-jobs.json'} "
        f"--workflow-profile candidate_workflow_profile.yml --applied-jobs {out_dir / f'{today}-applied-jobs.json'} "
        f"--out {out_dir} --date {today}"
    )
    lines.append("")
    lines.append("Then run the fail-closed resume and cover-letter production stage:")
    lines.append("")
    lines.append(
        f"scripts/jobagent_python.sh scripts/produce_resumes.py --resume-ready {out_dir / f'{today}-resume-ready-jobs.md'} "
        f"--applied-jobs {out_dir / f'{today}-applied-jobs.json'} --source-dir 'Source Docs' --out {out_dir / 'resumes'}"
    )
    lines.append("")
    lines.append(
        "This stage must use `resume-ready-jobs.md` as its sole authorization queue. If the exact job posting already "
        "has a validated resume in a prior production manifest, reuse and link that prior artifact instead of "
        "regenerating it. Treat different job IDs as different roles. Generate and validate one cover letter for each "
        "exact Resume Ready posting using the same applied snapshot and `$jhaynes-writing-voice`. Keep every claim "
        "grounded in verified candidate evidence, and regenerate legacy letters that do not match the approved voice. "
        "Validate every DOCX and rendered page, and "
        "regenerate the workflow HTML only after all authorized artifacts pass."
    )
    lines.append("")
    lines.append("Then export and publish the strict workflow observations to jobtracker analytics:")
    lines.append("")
    lines.append(
        f"scripts/jobagent_python.sh scripts/publish_skill_gap_analytics.py "
        f"--workflow-review {out_dir / f'{today}-workflow-review.json'} "
        f"--workflow-profile candidate_workflow_profile.yml --out {out_dir} --date {today} "
        f"--tracker-url http://127.0.0.1:8086/api/analytics/workflow-runs --ssh-host jobtracker-host"
    )
    lines.append("")
    lines.append(
        "This analytics stage writes the dated contract artifact before importing it. An analytics export or import "
        "failure must be reported explicitly, but it must not block or undo resume production, which has already "
        "completed from the strict authorization queue. Retrying the same date is idempotent."
    )
    lines.append("")
    lines.append(
        "Use the resulting `resume-ready-jobs.md` file as the only queue for resume generation or cover-letter generation. "
        "Use `human-review-packets.md` for gap questions and manual overrides."
    )
    lines.append(
        "Summarize `human-review-packets.md` under the exact final-report heading "
        "`## Gap Questions for Candidate`."
    )
    lines.append(
        "Do not recommend creating a resume for any job that is not listed in `resume-ready-jobs.md`."
    )
    lines.append("")

    discovered_jobs = out_dir / f"{today}-discovered-jobs.json"
    inspection_queue = out_dir / f"{today}-job-inspection-queue.md"
    workflow_review = out_dir / f"{today}-workflow-review.json"
    workflow_review_html = out_dir / f"{today}-workflow-review.html"
    human_review_packets = out_dir / f"{today}-human-review-packets.md"
    resume_ready_jobs = out_dir / f"{today}-resume-ready-jobs.md"
    resume_production = out_dir / f"{today}-resume-production.json"
    skill_gap_observations = out_dir / f"{today}-skill-gap-observations.json"
    skill_gap_import_result = out_dir / f"{today}-skill-gap-import-result.json"
    applied_jobs = out_dir / f"{today}-applied-jobs.json"
    ats_summary = out_dir / f"{today}-ats-discovery.md"
    public_queries = out_dir / f"{today}-public-job-board-queries.md"
    verified_public = out_dir / f"{today}-verified-public-board-jobs.md"
    x_posts = out_dir / f"{today}-x-job-posts.md"
    threads_posts = out_dir / f"{today}-threads-job-posts.md"
    shard_summary = out_dir / f"{today}-shard-summary.md"
    shard_manifest = out_dir / f"{today}-shard-manifest.json"
    shard_results = out_dir / f"{today}-shard-results.json"

    if (
        discovered_jobs.exists()
        or ats_summary.exists()
        or public_queries.exists()
        or verified_public.exists()
        or x_posts.exists()
        or threads_posts.exists()
        or shard_summary.exists()
        or shard_manifest.exists()
        or shard_results.exists()
        or workflow_review.exists()
        or workflow_review_html.exists()
        or human_review_packets.exists()
        or resume_ready_jobs.exists()
        or resume_production.exists()
        or skill_gap_observations.exists()
        or skill_gap_import_result.exists()
        or applied_jobs.exists()
    ):
        lines.append("## ATS-aware discovery inputs")
        lines.append("")
        if shard_summary.exists():
            lines.append(f"- Sharded discovery summary: `{shard_summary}`")
        if shard_manifest.exists():
            lines.append(f"- Shard manifest: `{shard_manifest}`")
        if shard_results.exists():
            lines.append(f"- Shard execution results: `{shard_results}`")
        if discovered_jobs.exists():
            lines.append(f"- Normalized discovered jobs: `{discovered_jobs}`")
        if inspection_queue.exists():
            lines.append(f"- Deterministic broad inspection queue: `{inspection_queue}`")
        if workflow_review.exists():
            lines.append(f"- Strict workflow review: `{workflow_review}`")
        if workflow_review_html.exists():
            lines.append(f"- Strict workflow review HTML: `{workflow_review_html}`")
        if human_review_packets.exists():
            lines.append(f"- Human review packets: `{human_review_packets}`")
        if resume_ready_jobs.exists():
            lines.append(f"- Resume-ready jobs: `{resume_ready_jobs}`")
        if resume_production.exists():
            lines.append(f"- Resume production manifest: `{resume_production}`")
        if skill_gap_observations.exists():
            lines.append(f"- Skill-gap analytics observations: `{skill_gap_observations}`")
        if skill_gap_import_result.exists():
            lines.append(f"- Skill-gap analytics import result: `{skill_gap_import_result}`")
        if applied_jobs.exists():
            lines.append(f"- Applied-jobs snapshot: `{applied_jobs}`")
        if ats_summary.exists():
            lines.append(f"- ATS discovery summary: `{ats_summary}`")
        if verified_public.exists():
            lines.append(f"- Verified public-board discoveries: `{verified_public}`")
        if public_queries.exists():
            lines.append(f"- Public job-board discovery queries: `{public_queries}`")
        if x_posts.exists():
            lines.append(f"- X.com job-post scan: `{x_posts}`")
        if threads_posts.exists():
            lines.append(f"- Threads job-post scan: `{threads_posts}`")
        lines.append("")
        lines.append(
        "Review ATS-discovered and verified public-board discovery candidates first. "
            "Use raw public-board queries only to discover additional official postings or "
            "confirm freshness; do not recommend unverified public-board URLs."
        )
        if inspection_queue.exists():
            lines.append(
                "Use the deterministic broad inspection queue as reviewer input and diagnostic evidence, not as the primary human-facing report."
            )
            lines.append(
                "The strict reviewer should carry forward active jobs from the latest strict workflow review even when discovery misses them today. "
                "Drop them from the active set after jobtracker shows they were applied."
            )
        if x_posts.exists():
            lines.append(
                "Use the X.com scan as a lightweight lead source only. It should capture only the post, the user, "
                "the post text, and whether the first visible same-user follow-up added more detail."
            )
        if threads_posts.exists():
            lines.append(
                "Use the Threads scan as a lightweight lead source only. It should capture only the post, the user, "
                "the post text, and whether the first visible same-user follow-up added more detail."
            )
        lines.append("")

    lines.append("## the candidate's fit profile")
    lines.append("")
    lines.append(format_profile_summary(profile))
    lines.append("")
    lines.append("## Search rules")
    lines.append("")
    lines.append(rules_text)
    lines.append("")
    lines.append("## Companies and suggested searches")
    lines.append("")
    detailed_companies = companies[:MAX_DETAILED_COMPANY_SECTIONS_IN_PLAN]
    remaining_companies = companies[MAX_DETAILED_COMPANY_SECTIONS_IN_PLAN:]
    lines.append(
        f"- Total target companies: {len(companies)}"
    )
    lines.append(
        f"- Detailed sections included below: {len(detailed_companies)}"
    )
    if remaining_companies:
        remaining_names = ", ".join(company["name"] for company in remaining_companies[:12])
        lines.append(
            f"- Additional companies not expanded in this plan: {len(remaining_companies)} "
            f"({remaining_names}{'...' if len(remaining_companies) > 12 else ''})"
        )
        lines.append("- Use `companies.yml` as the source of truth for the full company list.")
    lines.append("")

    for company in detailed_companies:
        lines.append(f"### {company['name']}")

        if company.get("careers_url"):
            lines.append(f"- Careers URL: {company['careers_url']}")

        if company.get("notes"):
            lines.append(f"- Notes: {company['notes']}")

        lines.append("")
        lines.append("Suggested searches:")

        for query in build_title_queries(company, profile):
            lines.append(f"- {query}")

        lines.append("")

    lines.append("## Required final report behavior")
    lines.append("")
    lines.append("For every discovered job, retain the source score internally and run the strict workflow reviewer.")
    lines.append("For resume workflow, use only jobs that pass the strict workflow reviewer.")
    lines.append(
        "Do not generate resumes directly from Apply/Maybe. Generate resumes only from the strict "
        "`resume-ready-jobs.md` output."
    )
    lines.append("Apply the same strict gate before generating any cover letter.")
    lines.append(
        "Summarize the strict `human-review-packets.md` output under `## Gap Questions for Candidate`, "
        "grouping duplicate questions when practical while preserving the affected company and role names."
    )
    lines.append(
        "Do not recommend creating a resume for any job that is not listed in `resume-ready-jobs.md`."
    )
    lines.append("")
    lines.append(
        "The strict workflow HTML is the primary human-facing report. Its action queue must contain Resume Ready, "
        "Gap Questions for Candidate, Already Applied, and Companies to Check."
    )
    lines.append("")
    lines.append(
        "Keep the deterministic broad inspection queue as a supporting diagnostic artifact. "
        "Label its first-stage buckets `Discovery Candidates — Higher Signal` and `Discovery Candidates — Review Signal`."
    )
    lines.append(
        "Do not present broad discovery counts as application recommendations. Put Moderate Stretch, Extreme Stretch, Skip, "
        "and links to raw discovery artifacts inside the collapsed diagnostic archive."
    )
    lines.append(
        "Also carry forward active jobs from the latest strict workflow review when the candidate has not applied yet. "
        "Every displayed current or carry-forward role must have a strict workflow status."
    )
    lines.append(
        "Treat manually surfaced official live job URLs from the candidate as candidate inputs for the dated report even if discovery missed them. "
        "Verify, score, include qualifying roles, and carry them forward until applied, delisted, or disqualified."
    )
    lines.append(
        "Treat browser-reviewed job findings from LinkedIn, Indeed, or any other site the same way: preserve the discovery source, "
        "resolve to the official posting, add the official URL to `manual_job_urls.yml`, and include the role in the current dated reports."
    )
    lines.append(
        "Treat AI Apply MCP results the same way: preserve AI Apply as the source, deduplicate before deep review, resolve a live official "
        "company or ATS posting, and keep unresolved results as verification items only. Scheduled runs are read-only and must never call "
        "`queue_job_for_review` or `submit_application`."
    )
    lines.append(
        "If a role is strong but either missing an official posting date or verified live beyond the 60-day window, keep it in a secondary `Needs Freshness Verification` section rather than discarding it."
    )
    lines.append("")
    lines.append("Treat Higher Signal and Review Signal as discovery labels only, never resume authorization.")
    lines.append("")
    lines.append("For every discovery candidate retained in diagnostics, include:")
    lines.append("- Company")
    lines.append("- Job title")
    lines.append("- Direct URL")
    lines.append("- Already applied status")
    lines.append("- Location / work model")
    lines.append("- Posting freshness evidence")
    lines.append("- Fit rating")
    lines.append("- Why it fits")
    lines.append("- Risk flags")
    lines.append("- Questions for Candidate, if any")
    lines.append("- Whether to run the resume workflow")
    lines.append("")
    lines.append(
        "Order discovery candidate sections in the broad inspection queue by fit score first, then freshness, then company/title."
    )
    lines.append("")
    lines.append("For X.com and Threads, do not do deep thread crawling or job-page verification in the social pass.")
    lines.append("")
    lines.append("For every Skip role, include the exact reason.")
    lines.append("")
    lines.append("After the report is finalized, mark jobs that already exist in jobtracker:")
    lines.append("")
    lines.append(
        f"scripts/jobagent_python.sh scripts/mark_applied_jobs.py --daily-report {output_path.parent / f'{today}-daily-job-search.md'} "
        f"--ssh-host {DEFAULT_JOBTRACKER_SSH_HOST} --applied-jobs-out {out_dir / f'{today}-applied-jobs.json'}"
    )
    lines.append(
        "If this final reconciliation changes the snapshot, rerun the strict reviewer and resume-production stage so applied jobs remain excluded."
    )
    lines.append("")
    lines.append("After the report is finalized, run the networking pass:")
    lines.append("")
    lines.append(
        f"scripts/jobagent_python.sh scripts/match_network.py --daily-report {output_path.parent / f'{today}-daily-job-search.md'} "
        f"--out {out_dir} --date {today}"
    )
    lines.append("")
    lines.append("Then live-verify top contacts tied to Apply jobs from the candidate's logged-in LinkedIn browser session.")
    lines.append(
        f"Write those results to `{out_dir / f'{today}-linkedin-live-verifications.json'}` as a JSON list with "
        "`linkedin_url`, `current_company`, `current_role`, and `verified_on`."
    )
    lines.append("")
    lines.append("Rerun the networking pass after live verification so contact status is refreshed in markdown and HTML:")
    lines.append("")
    lines.append(
        f"scripts/jobagent_python.sh scripts/match_network.py --daily-report {output_path.parent / f'{today}-daily-job-search.md'} "
        f"--out {out_dir} --date {today} "
        f"--linkedin-live-verifications {out_dir / f'{today}-linkedin-live-verifications.json'}"
    )
    lines.append("")
    lines.append(
        "The networking pass should add People to Contact and Introduction Messages "
        "for active candidate companies using the candidate's LinkedIn Connections.csv export."
    )
    lines.append(
        "Mark each contact as `export-confirmed` or `live-profile-confirmed` depending on whether "
        "the current company was confirmed from the CSV export alone or from the live LinkedIn profile."
    )
    if human_search_companies:
        lines.append("")
        lines.append("## Human Search Calibration")
        lines.append("")
        lines.append(
            "At the end of this run, include these five companies as a manual search checklist. "
            "the candidate will search them by hand and compare any jobs found against the agent output."
        )
        lines.append("")
        lines.extend(format_human_search_companies(human_search_companies))

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def write_report_template(
    *,
    today: str,
    out_dir: Path,
    human_search_companies: list[dict[str, Any]] | None = None,
) -> Path:
    output_path = out_dir / f"{today}-daily-job-search.md"

    if output_path.exists():
        return output_path

    lines = [
        f"# Daily Job Search Report - {today}",
        "",
        "## Discovery Candidates — Higher Signal",
        "",
        "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
        "|---|---|---|---|---|---|---|---|",
        "",
        "## Discovery Candidates — Review Signal",
        "",
        "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
        "|---|---|---|---|---|---|---|---|",
        "",
        "## Skip",
        "",
        "| Company | Role | Reason | URL |",
        "|---|---|---|---|",
        "",
        "## Needs Freshness Verification",
        "",
        "| Priority | Company | Role | Public Freshness Signal | Location / Work Model | Fit | Reason | URL |",
        "|---|---|---|---|---|---|---|---|",
        "",
        "## Gap Questions for Candidate",
        "",
        "- ",
        "",
        "## X.com Posts to Review",
        "",
        "| Posted | Search Query | User | Post Text | Same-User Follow-up | Post URL |",
        "|---|---|---|---|---|---|",
        "",
        "## Threads Posts to Review",
        "",
        "| Posted | Search Query | User | Post Text | Same-User Follow-up | Post URL |",
        "|---|---|---|---|---|---|",
        "",
        "## Notes",
        "",
        "- Keep roles older than 60 days out of Apply/Maybe; when the official page is still live and applyable, place them in Needs Freshness Verification.",
        "- Do not include delisted or 404 roles.",
        "- Prefer official company job URLs.",
        "- Do not recommend stretch jobs.",
        "- Carry forward active roles from the latest strict workflow review until the candidate applies or the role is clearly no longer usable.",
        "- Remove roles from the active carry-forward set after jobtracker marks them as already applied.",
        "- Treat manually surfaced official live job URLs as valid candidate inputs for the current report after live verification.",
        "- Treat browser-reviewed job findings from LinkedIn, Indeed, or any other site as manual intake: preserve the source, resolve the official URL, and include qualifying roles in this dated report.",
        "- Use AI Apply MCP only for bounded read-only discovery and application-status reconciliation; resolve an official company or ATS posting before promotion, never treat an AI Apply internal job ID as an employer requisition ID, and never call queue_job_for_review or submit_application.",
        "- Carry manually surfaced roles forward until applied, delisted, or disqualified.",
        "- If an official posting lacks a visible date, keep the displayed date honest but treat the role as freshest for ranking.",
        "- Social scans should stay shallow and cheap: no deep thread crawling, no per-link job verification.",
        "- Call out when a search returned no results.",
        "- Keep broad discovery as a diagnostic artifact; the strict workflow HTML is the actionable report.",
        "- Do not run Word resume or cover-letter generation unless the role appears in the strict resume-ready jobs output.",
        "- Summarize the strict human review packets here, preserving the affected company and role for each gap question.",
        "",
        "## Human Search Calibration",
        "",
        "Manually search these company career sites and compare any roles found against this report.",
        "",
    ]
    if human_search_companies:
        lines.extend(format_human_search_companies(human_search_companies))
    else:
        lines.append("- ")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a daily Codex job-search plan."
    )
    parser.add_argument("--companies", default="companies.yml")
    parser.add_argument("--profile", default="candidate_profile.yml")
    parser.add_argument("--rules", default="search_rules.md")
    parser.add_argument("--job-overrides", default="job_overrides.yml")
    parser.add_argument("--out", default="outputs")

    args = parser.parse_args()

    companies_doc = load_yaml(Path(args.companies))
    overrides_doc = load_yaml(Path(args.job_overrides))
    profile_doc = load_yaml(Path(args.profile))
    rules_text = read_text(Path(args.rules))

    companies = normalize_company_entries(companies_doc)
    raw_overrides = overrides_doc.get("overrides", []) if isinstance(overrides_doc, dict) else overrides_doc
    if not isinstance(raw_overrides, list) or not all(isinstance(item, dict) for item in raw_overrides):
        raise ValueError("job_overrides.yml must contain a top-level 'overrides' list")
    run_date = dt.date.today()
    companies = filter_companies_by_overrides(companies, raw_overrides, as_of=run_date)
    profile = get_profile_section(profile_doc)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    today = run_date.isoformat()
    human_search_companies = write_human_search_rotation(
        today=today,
        companies=companies,
        out_dir=out_dir,
    )

    search_plan = write_search_plan(
        today=today,
        companies=companies,
        profile=profile,
        rules_text=rules_text,
        out_dir=out_dir,
        human_search_companies=human_search_companies,
    )

    report_template = write_report_template(
        today=today,
        out_dir=out_dir,
        human_search_companies=human_search_companies,
    )

    print(f"Wrote search plan: {search_plan}")
    print(f"Wrote report template: {report_template}")
    print(f"Wrote human search rotation: {out_dir / f'{today}-human-search-companies.md'}")


if __name__ == "__main__":
    main()
