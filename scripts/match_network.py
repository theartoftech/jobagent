#!/usr/bin/env python3
"""
Match recommended jobs to LinkedIn connections.

The LinkedIn Connections.csv export exposes current company/title fields for
connections. It does not reliably include each connection's employment history,
so this script only claims current-company matches unless the visible exported
fields contain additional evidence.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from scripts.render_daily_job_search_html import write_html_report
except ModuleNotFoundError:
    from render_daily_job_search_html import write_html_report


DEFAULT_LINKEDIN_EXPORT = Path("linkedin-export.zip")

SECTION_START = "<!-- networking-pass:start -->"
SECTION_END = "<!-- networking-pass:end -->"
REPORT_SECTION_DECISIONS = {
    "Apply": "Apply",
    "Maybe": "Maybe",
    "Discovery Candidates — Higher Signal": "Apply",
    "Discovery Candidates — Review Signal": "Maybe",
}


@dataclass
class Job:
    decision: str
    company: str
    title: str
    posted: str
    location: str
    fit: str
    url: str
    already_applied: str = "No"


@dataclass
class Contact:
    first_name: str
    last_name: str
    linkedin_url: str
    email: str
    company: str
    position: str
    connected_on: str

    @property
    def name(self) -> str:
        return " ".join(part for part in [self.first_name, self.last_name] if part).strip()


@dataclass
class ContactMatch:
    company: str
    contact: Contact
    current_company: str
    current_role: str
    match_source: str
    priority_score: int
    why: str


@dataclass(frozen=True)
class LiveProfileVerification:
    linkedin_url: str
    current_company: str
    current_role: str
    verified_on: str


def normalize_company(value: str) -> str:
    normalized = value.lower()
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    stop_words = {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "company",
        "co",
        "group",
        "holdings",
    }
    tokens = [token for token in normalized.split() if token not in stop_words]
    return " ".join(tokens)


def company_aliases(company: str) -> set[str]:
    aliases = {normalize_company(company)}
    explicit = {
        "global payments": {"global payments", "global payments inc"},
        "childrens healthcare of atlanta": {
            "childrens healthcare of atlanta",
            "choa",
            "children s healthcare of atlanta",
        },
        "ge healthcare": {"ge healthcare", "ge health care"},
        "ge aerospace": {"ge aerospace", "general electric aerospace"},
        "ge vernova": {"ge vernova", "general electric vernova"},
        "tata consultancy services": {"tata consultancy services", "tcs"},
        "lockheed martin": {"lockheed martin", "lockheed martin corporation"},
        "aig": {"aig", "american international group"},
        "cvs health": {"cvs health", "cvs"},
    }
    aliases.update(normalize_company(item) for item in explicit.get(normalize_company(company), set()))
    return {alias for alias in aliases if alias}


def canonicalize_linkedin_url(value: str) -> str:
    url = (value or "").strip()
    if not url:
        return ""
    url = url.split("#", 1)[0].split("?", 1)[0]
    return url.rstrip("/").lower()


def load_live_verifications(path: Path | None) -> dict[str, LiveProfileVerification]:
    if path is None or not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Live LinkedIn verification file must contain a JSON list.")

    verifications: dict[str, LiveProfileVerification] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        linkedin_url = canonicalize_linkedin_url(str(item.get("linkedin_url", "")))
        current_company = str(item.get("current_company", "")).strip()
        if not linkedin_url or not current_company:
            continue
        verifications[linkedin_url] = LiveProfileVerification(
            linkedin_url=linkedin_url,
            current_company=current_company,
            current_role=str(item.get("current_role", "")).strip(),
            verified_on=str(item.get("verified_on", "")).strip(),
        )
    return verifications


def read_connections(path: Path) -> list[Contact]:
    if not path.exists():
        raise FileNotFoundError(f"LinkedIn export not found: {path}")

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            with archive.open("Connections.csv") as handle:
                text = handle.read().decode("utf-8-sig", errors="replace")
    else:
        text = path.read_text(encoding="utf-8-sig")

    lines = text.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("First Name,Last Name,URL,Email Address,Company,Position,Connected On")
        ),
        None,
    )
    if header_index is None:
        raise ValueError("Could not find Connections.csv header row in LinkedIn export.")

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    contacts: list[Contact] = []
    for row in reader:
        if not row.get("First Name") and not row.get("Last Name"):
            continue
        contacts.append(
            Contact(
                first_name=(row.get("First Name") or "").strip(),
                last_name=(row.get("Last Name") or "").strip(),
                linkedin_url=(row.get("URL") or "").strip(),
                email=(row.get("Email Address") or "").strip(),
                company=(row.get("Company") or "").strip(),
                position=(row.get("Position") or "").strip(),
                connected_on=(row.get("Connected On") or "").strip(),
            )
        )
    return contacts


def parse_recommended_jobs(report_path: Path) -> list[Job]:
    if not report_path.exists():
        raise FileNotFoundError(f"Daily report not found: {report_path}")

    current_section = ""
    jobs: list[Job] = []
    for line in report_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped.removeprefix("## ").strip()
            continue
        if current_section not in REPORT_SECTION_DECISIONS:
            continue
        if not stripped.startswith("|") or is_table_separator(stripped) or "Priority" in stripped:
            continue
        columns = [column.strip() for column in stripped.strip("|").split("|")]
        if len(columns) < 7:
            continue
        jobs.append(
            Job(
                decision=REPORT_SECTION_DECISIONS[current_section],
                company=columns[1],
                title=columns[2],
                posted=columns[3],
                location=columns[4],
                fit=columns[5],
                url=columns[6],
                already_applied=columns[7] if len(columns) > 7 else "No",
            )
        )
    return jobs


def is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def score_contact(current_company: str, current_role: str, jobs: list[Job]) -> tuple[int, str]:
    haystack = f"{current_role} {current_company}".lower()
    score = 50
    reasons: list[str] = []

    if any(token in haystack for token in ["talent acquisition", "recruiter", "recruiting"]):
        score += 30
        reasons.append("hiring-channel contact")
    if any(token in haystack for token in ["software", "engineer", "architect", "platform", "technical"]):
        score += 25
        reasons.append("technical contact")
    if any(token in haystack for token in ["director", "principal", "staff", "lead", "vp", "head"]):
        score += 15
        reasons.append("senior/internal-routing contact")
    if any(token in haystack for token in ["consultant", "professional services", "implementation", "solution"]):
        score += 15
        reasons.append("services/advisory-lane contact")
    if any(job.decision == "Apply" for job in jobs):
        score += 10
        reasons.append("company has Apply recommendation")

    if not reasons:
        reasons.append("current-company connection")

    return min(score, 100), "; ".join(reasons)


def match_contacts(
    jobs: list[Job],
    contacts: list[Contact],
    verifications: dict[str, LiveProfileVerification],
) -> dict[str, list[ContactMatch]]:
    jobs_by_company: dict[str, list[Job]] = {}
    company_alias_map: dict[str, set[str]] = {}
    for job in jobs:
        jobs_by_company.setdefault(job.company, []).append(job)
        company_alias_map[job.company] = company_aliases(job.company)

    matches: dict[str, list[ContactMatch]] = {company: [] for company in jobs_by_company}
    for contact in contacts:
        verification = verifications.get(canonicalize_linkedin_url(contact.linkedin_url))
        current_company = (
            verification.current_company.strip()
            if verification and verification.current_company.strip()
            else contact.company
        )
        current_role = (
            verification.current_role.strip()
            if verification and verification.current_role.strip()
            else contact.position
        )
        match_source = "live-profile-confirmed" if verification else "export-confirmed"
        contact_company = normalize_company(current_company)
        if not contact_company:
            continue
        for company, aliases in company_alias_map.items():
            if contact_company in aliases:
                score, why = score_contact(current_company, current_role, jobs_by_company[company])
                matches[company].append(
                    ContactMatch(
                        company=company,
                        contact=contact,
                        current_company=current_company,
                        current_role=current_role,
                        match_source=match_source,
                        priority_score=score,
                        why=why,
                    )
                )

    for company in matches:
        matches[company].sort(
            key=lambda item: (
                -item.priority_score,
                parse_connected_on(item.contact.connected_on) or dt.date.min,
                item.contact.name.lower(),
            ),
            reverse=False,
        )
    return matches


def parse_connected_on(value: str) -> dt.date | None:
    for fmt in ["%d %b %Y", "%d %B %Y", "%Y-%m-%d"]:
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def markdown_link(label: str, url: str) -> str:
    if not url:
        return label
    return f"[{escape_md(label)}]({url})"


def escape_md(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def format_jobs(jobs: Iterable[Job]) -> list[str]:
    lines: list[str] = []
    for job in jobs:
        lines.append(
            f"- {job.decision}: {escape_md(job.title)}; {escape_md(job.posted)}; "
            f"{escape_md(job.location)}; fit {escape_md(job.fit)}; {job.url}"
        )
    return lines


def summarize_intro_titles(jobs: list[Job]) -> str:
    unique_titles: list[str] = []
    seen: set[str] = set()
    for job in jobs:
        normalized = job.title.strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_titles.append(job.title.strip())
        if len(unique_titles) >= 3:
            break

    if not unique_titles:
        return "a role"
    if len(unique_titles) == 1:
        return unique_titles[0]
    if len(unique_titles) == 2:
        return f"a couple of {jobs[0].company} roles, including {unique_titles[0]} and {unique_titles[1]}"
    return (
        f"a few {jobs[0].company} roles, including {unique_titles[0]}, "
        f"{unique_titles[1]}, and {unique_titles[2]}"
    )


def intro_message(contact: Contact, company: str, jobs: list[Job]) -> str:
    role_summary = summarize_intro_titles(jobs)
    first_name = contact.first_name or contact.name.split()[0]
    candidate_name = os.environ.get("JOBAGENT_CANDIDATE_NAME", "Candidate Name")
    background = os.environ.get(
        "JOBAGENT_NETWORKING_BACKGROUND",
        "senior software engineering, backend systems, APIs, enterprise integrations, "
        "production troubleshooting, and technical leadership",
    )
    return (
        f"Hi {first_name},\n\n"
        f"I noticed you are at {company} and wanted to reach out. I am looking at "
        f"{role_summary} and wanted to sanity-check whether my background is relevant internally. "
        f"My background includes {background}.\n\n"
        "Would you be open to pointing me toward the right hiring team or giving a quick read on "
        "whether this looks like a reasonable fit before I apply?\n\n"
        "Thanks,\n"
        f"{candidate_name}"
    )


def is_already_applied(job: Job) -> bool:
    value = job.already_applied.strip().casefold()
    return bool(value and value not in {"no", "n", "-"})


def recruiter_note_jobs(jobs: list[Job]) -> dict[str, list[Job]]:
    jobs_by_company: dict[str, list[Job]] = {}
    for job in jobs:
        if job.decision == "Apply" and not is_already_applied(job):
            jobs_by_company.setdefault(job.company, []).append(job)
    return jobs_by_company


def recruiter_add_note(company: str, jobs: list[Job]) -> str:
    role_summary = summarize_intro_titles(jobs)
    return (
        f"Hi, I am exploring {company} roles including {role_summary}. "
        "My background is Java/Spring backend, REST APIs, SQL, enterprise integrations, "
        "Oracle systems, and technical leadership. Could you point me to the right recruiter?"
    )


def build_networking_markdown(
    *,
    today: dt.date,
    jobs: list[Job],
    matches: dict[str, list[ContactMatch]],
    linkedin_export: Path,
) -> str:
    jobs_by_company: dict[str, list[Job]] = {}
    for job in jobs:
        jobs_by_company.setdefault(job.company, []).append(job)

    lines: list[str] = []
    lines.append(f"# Networking Matches - {today.isoformat()}")
    lines.append("")
    lines.append(f"Source: LinkedIn export `{linkedin_export}`.")
    lines.append("")
    lines.append(
        "Limitation: LinkedIn `Connections.csv` exposes current company/title fields, "
        "not full employment history for each connection. Matches below are current-company matches."
    )
    lines.append("")

    if not jobs:
        lines.append("No Apply or Maybe jobs were found in the daily report.")
        return "\n".join(lines)

    lines.append("## LinkedIn Recruiter Add Notes")
    lines.append("")
    note_jobs_by_company = recruiter_note_jobs(jobs)
    if not note_jobs_by_company:
        lines.append("No unapplied Apply jobs need recruiter connection notes.")
        lines.append("")
    else:
        for company, company_jobs in note_jobs_by_company.items():
            lines.append(f"### {company}")
            lines.append("")
            lines.append(recruiter_add_note(company, company_jobs))
            lines.append("")

    lines.append("## People to Contact")
    lines.append("")
    for company, company_jobs in jobs_by_company.items():
        lines.append(f"### {company}")
        lines.append("")
        lines.append("Relevant jobs:")
        lines.extend(format_jobs(company_jobs))
        lines.append("")
        company_matches = matches.get(company, [])
        if not company_matches:
            lines.append("No current-company LinkedIn contacts found.")
            lines.append("")
            continue
        lines.append("| Priority | Name | Current Company | Current Role | LinkedIn | Connected On | Match Source | Why Reach Out |")
        lines.append("|---:|---|---|---|---|---|---|---|")
        for index, match in enumerate(company_matches[:8], start=1):
            contact = match.contact
            lines.append(
                f"| {index} | {escape_md(contact.name)} | {escape_md(match.current_company)} | "
                f"{escape_md(match.current_role)} | {markdown_link('Profile', contact.linkedin_url)} | "
                f"{escape_md(contact.connected_on)} | {escape_md(match.match_source)} | {escape_md(match.why)} |"
            )
        lines.append("")

    lines.append("## Introduction Messages")
    lines.append("")
    for company, company_jobs in jobs_by_company.items():
        company_matches = matches.get(company, [])
        for match in company_matches[:3]:
            contact = match.contact
            lines.append(f"### {company} - {contact.name}")
            lines.append("")
            lines.append(intro_message(contact, company, company_jobs))
            lines.append("")

    return "\n".join(lines)


def build_daily_section(networking_markdown: str) -> str:
    lines = networking_markdown.splitlines()
    try:
        start = lines.index("## LinkedIn Recruiter Add Notes")
    except ValueError:
        return "\n".join(
            [
                SECTION_START,
                "## LinkedIn Recruiter Add Notes",
                "",
                "No recruiter connection notes generated.",
                "",
                "## People to Contact",
                "",
                "No networking matches generated.",
                SECTION_END,
            ]
        )

    compact: list[str] = [SECTION_START]
    compact.extend(lines[start:])
    compact.append(SECTION_END)
    return "\n".join(compact)


def update_daily_report(report_path: Path, section: str) -> None:
    text = report_path.read_text(encoding="utf-8")
    if SECTION_START in text and SECTION_END in text:
        pattern = re.compile(
            re.escape(SECTION_START) + r".*?" + re.escape(SECTION_END),
            flags=re.DOTALL,
        )
        text = pattern.sub(section, text)
    else:
        text = text.rstrip() + "\n\n" + section + "\n"
    report_path.write_text(text, encoding="utf-8")


def run(
    *,
    linkedin_export: Path,
    linkedin_live_verifications: Path | None,
    daily_report: Path,
    out_dir: Path,
    today: dt.date,
    update_report: bool,
) -> Path:
    contacts = read_connections(linkedin_export)
    jobs = parse_recommended_jobs(daily_report)
    verifications = load_live_verifications(linkedin_live_verifications)
    matches = match_contacts(jobs, contacts, verifications)
    out_dir.mkdir(parents=True, exist_ok=True)

    networking_markdown = build_networking_markdown(
        today=today,
        jobs=jobs,
        matches=matches,
        linkedin_export=linkedin_export,
    )
    output_path = out_dir / f"{today.isoformat()}-networking-matches.md"
    output_path.write_text(networking_markdown, encoding="utf-8")

    if update_report:
        update_daily_report(daily_report, build_daily_section(networking_markdown))
    write_html_report(report_path=daily_report, networking_path=output_path)

    return output_path


def main() -> None:
    today = dt.date.today()
    parser = argparse.ArgumentParser(description="Match recommended jobs to LinkedIn contacts.")
    parser.add_argument(
        "--linkedin-export",
        type=Path,
        default=Path(os.environ.get("JOBAGENT_LINKEDIN_EXPORT", str(DEFAULT_LINKEDIN_EXPORT))),
    )
    parser.add_argument("--linkedin-live-verifications", type=Path)
    parser.add_argument("--daily-report", type=Path, default=Path("outputs") / f"{today.isoformat()}-daily-job-search.md")
    parser.add_argument("--out", type=Path, default=Path("outputs"))
    parser.add_argument("--date", default=today.isoformat())
    parser.add_argument(
        "--no-update-report",
        action="store_true",
        help="Write the standalone networking report only; do not append/update the daily report.",
    )
    args = parser.parse_args()

    run_date = dt.date.fromisoformat(args.date)
    live_verification_path = (
        args.linkedin_live_verifications
        if args.linkedin_live_verifications is not None
        else args.out / f"{run_date.isoformat()}-linkedin-live-verifications.json"
    )
    output_path = run(
        linkedin_export=args.linkedin_export,
        linkedin_live_verifications=live_verification_path,
        daily_report=args.daily_report,
        out_dir=args.out,
        today=run_date,
        update_report=not args.no_update_report,
    )
    print(f"Wrote networking matches: {output_path}")
    if not args.no_update_report:
        print(f"Updated daily report: {args.daily_report}")


if __name__ == "__main__":
    main()
