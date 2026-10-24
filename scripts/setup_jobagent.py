"""Validate interview answers and create private, candidate-owned workflow inputs."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

OUTPUT_FILES = (
    "candidate_profile.yml", "candidate_workflow_profile.yml", "companies.yml",
    "ats_registry.yml", "startup_watchlist.yml", "manual_job_urls.yml", "job_overrides.yml",
    "job_sources.yml", "search_rules.md", "applied_jobs.json", "run_preferences.json",
    "candidate_evidence.json", "resume_templates.json", ".env.local",
)
LIST_FIELDS = (
    "title_targets", "adjacent_titles", "strong_keywords", "secondary_keywords", "evidence",
    "do_not_claim", "languages", "unsupported_required_languages", "commute_locations", "countries",
    "work_models", "employment_types", "excluded_companies", "excluded_titles", "exclude_if_primary_focus",
)
TEXT_FIELDS = ("name", "email", "last_role", "city", "metro", "currency", "work_authorization", "travel", "relocation", "timezone", "schedule")


def public_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Site and posting URLs must be strings")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a complete HTTPS URL without credentials")
    return value.rstrip("/")


def validate_answers(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("Interview answers must be a JSON object")
    for key in LIST_FIELDS:
        value = data.get(key)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"{key} must be a list of nonempty strings")
    for key in ("title_targets", "strong_keywords", "evidence", "languages", "countries", "work_models"):
        if not data[key]:
            raise ValueError(f"Interview incomplete: {key} cannot be empty")
    for key in TEXT_FIELDS:
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Interview incomplete: {key} must be nonempty text")
    if set(data["work_models"]) - {"remote", "hybrid", "onsite"}:
        raise ValueError("work_models supports remote, hybrid, onsite")
    if set(data["work_models"]) & {"hybrid", "onsite"} and not data["commute_locations"]:
        raise ValueError("Onsite/hybrid searches need confirmed commute_locations")
    if {x.casefold() for x in data["languages"]} & {x.casefold() for x in data["unsupported_required_languages"]}:
        raise ValueError("A language cannot be both supported and unsupported")
    for key in ("sponsorship_required", "schedule_enabled", "document_generation"):
        if not isinstance(data.get(key), bool):
            raise ValueError(f"{key} must be a boolean")
    if data["schedule_enabled"]:
        raise ValueError("Setup records a proposed schedule only; activate it separately after the first run")
    salary = data.get("salary_floor")
    if type(salary) not in (int, float) or not math.isfinite(salary) or salary < 0:
        raise ValueError("salary_floor must be a nonnegative finite number; 0 means no floor")
    if type(data.get("freshness_days")) is not int or not 1 <= data["freshness_days"] <= 365:
        raise ValueError("freshness_days must be an integer between 1 and 365")
    try:
        ZoneInfo(data["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone must be an IANA zone such as Europe/Berlin") from exc
    companies = data.get("companies")
    if not isinstance(companies, list) or not 1 <= len(companies) <= 199:
        raise ValueError("Choose 1–199 companies; LinkedIn occupies one of the maximum 200 search sites")
    seen_names: set[str] = set()
    seen_sites: set[str] = set()
    for item in companies:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip() for k in ("name", "reason", "verified_on")):
            raise ValueError("Each company needs name, reason, careers_url, evidence_url, verified_on")
        url = public_url(item.get("careers_url"))
        public_url(item.get("evidence_url"))
        try:
            dt.date.fromisoformat(item["verified_on"])
        except ValueError as exc:
            raise ValueError("verified_on must be YYYY-MM-DD") from exc
        if item["name"].casefold() in seen_names or url.casefold() in seen_sites:
            raise ValueError("Deduplicate company names and career sites before setup")
        seen_names.add(item["name"].casefold())
        seen_sites.add(url.casefold())
    for key in ("applied_jobs", "manual_jobs"):
        if not isinstance(data.get(key), list):
            raise ValueError(f"{key} must be a list (empty only after asking the candidate)")
        for item in data[key]:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip() for k in ("company", "title")):
                raise ValueError(f"{key} entries need company and title")
            public_url(item.get("job_url" if key == "applied_jobs" else "url"))
            if key == "applied_jobs" and not all(isinstance(item.get(k), str) for k in ("status", "applied_date")):
                raise ValueError("Applied jobs need status and applied_date strings")
    if not isinstance(data.get("resume_path"), str):
        raise ValueError("resume_path must be text; use empty text if no resume is available")
    if data["document_generation"]:
        raise ValueError("Enable documents separately after validating source DOCX templates and candidate evidence")


def configure(data: dict[str, Any], root: Path) -> None:
    validate_answers(data)
    conflicts = [name for name in OUTPUT_FILES if (root / name).exists()]
    if conflicts:
        raise FileExistsError("Refusing to overwrite private setup: " + ", ".join(conflicts))
    common = {key: data[key] for key in ("unsupported_required_languages", "excluded_companies", "work_models", "countries")}
    common["hybrid_allowed_metro_tokens"] = data["commute_locations"]
    common["locations"] = {"preferred": data["commute_locations"] + data["countries"], "acceptable": [], "reject": []}
    profile = {**common, "title_targets": data["title_targets"] + data["adjacent_titles"],
               "worth_applying_title_targets": [], "strong_keywords": data["strong_keywords"],
               "acceptable_secondary_keywords": data["secondary_keywords"],
               "exclude_if_primary_focus": data["exclude_if_primary_focus"],
               "excluded_role_title_markers": data["excluded_titles"],
               "do_not_claim": data["do_not_claim"], "languages": data["languages"],
               "compensation": {"target_minimum": data["salary_floor"], "currency": data["currency"]},
               "freshness": {"include_max_age_days": data["freshness_days"]}}
    workflow = {**common, "title_targets": profile["title_targets"], "salary_floor": data["salary_floor"] or None,
                "salary_currency": data["currency"], "confirmed_strengths": data["strong_keywords"],
                "allowed_with_care": data["secondary_keywords"], "do_not_claim": data["do_not_claim"],
                "excluded_role_title_markers": data["excluded_titles"], "hard_reject_if_primary": data["exclude_if_primary_focus"],
                "candidate_gap_resolutions": {skill: "do_not_claim" for skill in data["do_not_claim"]},
                "gap_resolutions": {}, "seniority_approvals": {}, "strategic_companies": [],
                "resume_templates": {}, "default_resume_template": "general", "resume_core_skills": data["strong_keywords"]}
    sources = [{"name": "LinkedIn Jobs", "url": "https://www.linkedin.com/jobs/", "type": "browser_assisted"}]
    sources += [{"name": c["name"], "url": c["careers_url"], "type": "official"} for c in data["companies"]]
    docs: dict[str, Any] = {
        "candidate_profile.yml": {"target_profile": profile}, "candidate_workflow_profile.yml": workflow,
        "companies.yml": {"companies": data["companies"]}, "ats_registry.yml": {"companies": {}},
        "startup_watchlist.yml": {"startups": []}, "manual_job_urls.yml": {"jobs": data["manual_jobs"]},
        "job_overrides.yml": {"overrides": []}, "job_sources.yml": {"sources": sources},
        "applied_jobs.json": {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "ssh_host": "local-candidate-record", "jobs": data["applied_jobs"]},
        "run_preferences.json": {key: data[key] for key in ("timezone", "schedule", "schedule_enabled", "freshness_days", "document_generation")},
        "candidate_evidence.json": {key: data[key] for key in ("name", "email", "last_role", "evidence", "resume_path", "languages", "city", "metro", "employment_types", "work_authorization", "sponsorship_required", "travel", "relocation")},
        "resume_templates.json": {},
    }
    # JSON quoting is deliberately not used for a shell file: it permits command substitution.
    docs[".env.local"] = "# Optional document contact settings; no API credentials are required.\n"
    docs["search_rules.md"] = (
        "# Candidate search rules\n\nRead candidate_evidence.json, both profiles, and run_preferences.json before every run.\n"
        "Only verified live employer/ATS postings can be recommended. Never invent qualifications or dates.\n"
        "Evaluate required versus preferred criteria, spoken/written languages, country restrictions, work authorization, sponsorship, compensation currency, employment type, travel and relocation against the interview answers.\n"
        "Unknown eligibility or required experience means ask the candidate; a discovery score is not approval.\n"
        "Record every unmet requirement by exact posting URL in the workflow profile before strict review.\n"
        "Maintain local applied_jobs.json. Never submit applications, contact employers or publish data automatically.\n"
        "Keep at most 200 distinct sites across companies, startup watchlist and job sources. LinkedIn is browser assisted.\n"
        "Only the dated resume-ready queue permits documents after human qualification review.\n"
    )
    root.mkdir(parents=True, exist_ok=True)
    for name, value in docs.items():
        content = value if isinstance(value, str) else (json.dumps(value, indent=2) + "\n" if name.endswith(".json") else yaml.safe_dump(value, sort_keys=False, allow_unicode=True))
        (root / name).write_text(content, encoding="utf-8")


def check_setup(root: Path) -> int:
    """Fail before searching if private inputs are missing or the site budget grew."""
    for name in OUTPUT_FILES:
        if not (root / name).is_file():
            raise FileNotFoundError(f"Setup incomplete: {name}. Follow README.md.")
    sites: set[str] = set()
    for filename, key, url_key in (("companies.yml", "companies", "careers_url"),
                                  ("startup_watchlist.yml", "startups", "careers_url"),
                                  ("job_sources.yml", "sources", "url")):
        document = yaml.safe_load((root / filename).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get(key), list):
            raise ValueError(f"{filename} must contain a {key} list")
        for item in document[key]:
            if not isinstance(item, dict):
                raise ValueError(f"Invalid site entry in {filename}")
            sites.add(public_url(item.get(url_key)).casefold())
    registry = yaml.safe_load((root / "ats_registry.yml").read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or not isinstance(registry.get("companies"), dict):
        raise ValueError("ats_registry.yml must contain a companies mapping")
    for company in registry["companies"].values():
        if not isinstance(company, dict) or not isinstance(company.get("ats"), list):
            raise ValueError("Each ATS mapping must contain an ats list")
        for entry in company["ats"]:
            if not isinstance(entry, dict):
                raise ValueError("ATS entries must contain a URL")
            sites.add(public_url(entry.get("url")).casefold())
    if len(sites) > 200:
        raise ValueError(f"Maximum 200 distinct search sites; found {len(sites)}. Reduce the shortlist.")
    return len(sites)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate existing private files and the 200-site budget")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.check:
        print(f"Setup valid: {check_setup(args.root)} distinct search sites.")
        return
    if args.answers is None:
        parser.error("Provide --answers or --check")
    configure(json.loads(args.answers.read_text(encoding="utf-8")), args.root)
    print(f"Created {len(OUTPUT_FILES)} private configuration files. Follow README.md for the first run.")


if __name__ == "__main__":
    main()
