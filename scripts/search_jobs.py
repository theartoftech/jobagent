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
    titles = profile.get("title_targets", [])
    keywords = profile.get("strong_keywords", [])
    if not isinstance(titles, list) or not isinstance(keywords, list):
        raise ValueError("title_targets and strong_keywords must be lists")
    locations = profile.get("locations", {}).get("preferred", [])
    domains = company.get("official_search_domains", [])
    domains = domains or [domain_from_url(company.get("careers_url", ""))]
    return [f'{company["name"]} site:{domain} "{title}" ' + " ".join(keywords[:3] + locations[:3])
            for title in titles for domain in domains][:MAX_SUGGESTED_SEARCHES_PER_COMPANY_IN_PLAN]


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
    age = profile.get("freshness", {}).get("include_max_age_days", 30)
    lines = [f"# Job Search Plan — {today}", "", format_profile_summary(profile), "",
             "## Run instructions", "",
             "Read README.md, candidate_evidence.json and search_rules.md. Confirm the run date in the candidate's timezone.",
             "Run sharded ATS/company discovery first, then open LinkedIn Jobs in the tool's internal browser for additional hits.",
             "Ask the user to sign in if necessary. Do not automate authentication, apply, send messages, or bypass access restrictions.",
             "Resolve browser findings to live official employer/ATS postings before adding them to manual_job_urls.yml.",
             "Report unavailable sources explicitly. A generated plan is not a completed search.", "",
             "```bash",
             "scripts/jobagent_python.sh scripts/setup_jobagent.py --check",
             f"scripts/jobagent_python.sh scripts/discover_jobs_sharded.py --companies companies.yml --profile candidate_profile.yml --out {out_dir} --max-age-days {age} --ats-registry ats_registry.yml --manual-job-urls manual_job_urls.yml --startup-watchlist startup_watchlist.yml --startup-count 0 --pause-seconds 0",
             f"scripts/jobagent_python.sh scripts/build_job_inspection_queue.py --discovered-jobs {out_dir}/{today}-discovered-jobs.json --out {out_dir} --date {today}",
             f"scripts/jobagent_python.sh scripts/mark_applied_jobs.py --daily-report {out_dir}/{today}-daily-job-search.md --local-applied applied_jobs.json --applied-jobs-out {out_dir}/{today}-applied-jobs.json",
             "```", "",
             "Before strict review, inspect full official descriptions against confirmed candidate evidence. Record all unknown required qualifications, language fluency, work authorization, sponsorship, employment type, travel and compensation currency as gaps. Do not treat keyword scores as complete qualification checks.",
             "```bash",
             f"scripts/jobagent_python.sh scripts/review_job_fit.py --discovered-jobs {out_dir}/{today}-discovered-jobs.json --workflow-profile candidate_workflow_profile.yml --applied-jobs {out_dir}/{today}-applied-jobs.json --out {out_dir} --date {today}",
             "```", "",
             "Open workflow-review.html and human-review-packets.md. The dated resume-ready-jobs.md is the only document authorization queue. Documents and analytics are optional; see README.md.",
             "## Candidate rules", "", rules_text, "", "## Company searches", ""]
    for company in companies[:MAX_DETAILED_COMPANY_SECTIONS_IN_PLAN]:
        lines.append(f"### {company['name']}")
        lines.extend(f"- {query}" for query in build_title_queries(company, profile))
    if len(companies) > MAX_DETAILED_COMPANY_SECTIONS_IN_PLAN:
        lines.append("Additional companies not expanded in this plan; discovery still reads the full company file.")
    lines += ["", "## Human Search Calibration", *format_human_search_companies(human_search_companies or companies[:5])]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
        description="Generate a daily job-search plan."
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
