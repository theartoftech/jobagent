#!/usr/bin/env python3
"""
Annotate a daily job-search report with "Already Applied" status from jobtracker.

The script reads application history from the remote jobtracker SQLite database
through the configured SSH host, then updates the Apply and
Maybe tables in the Markdown report by appending an "Already Applied" column.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit


DEFAULT_JOBTRACKER_SSH_HOST = "jobtracker-host"
INTEREST_ONLY_STATUSES = {"interested", "archived", ""}


@dataclass
class TrackerJob:
    company: str
    title: str
    status: str
    job_url: str
    applied_date: str

    @property
    def already_applied(self) -> bool:
        normalized_status = normalize_text(self.status)
        return bool(self.applied_date) or normalized_status not in INTEREST_ONLY_STATUSES


@dataclass
class ReportJob:
    company: str
    title: str
    posted: str
    location: str
    fit: str
    url: str


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def canonicalize_url(value: str) -> str:
    url = (value or "").strip()
    if not url:
        return ""
    url = url.replace("https://", "").replace("http://", "")
    url = url.split("#", 1)[0]
    return url.rstrip("/").lower()


def extract_requisition_id(value: str) -> str:
    haystack = value or ""
    parsed = urlsplit(haystack)
    if parsed.netloc.casefold() == "jobs.intuit.com":
        path_segments = [segment for segment in parsed.path.split("/") if segment]
        if len(path_segments) >= 2 and path_segments[-1].isdigit() and len(path_segments[-1]) >= 5:
            return path_segments[-1]
    patterns = [
        r"\bREQ[_-]\d+\b",
        r"\bJR[-_]\d+\b",
        r"\bR[-_]\d+\b",
        r"(?<![A-Z0-9])R\d{6,}(?:-\d+)?(?!\d)",
        r"\bJ\d{6,}\b",
        r"\b\d{5,}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, haystack, flags=re.IGNORECASE)
        if match:
            return match.group(0).upper()
    return ""


def fetch_tracker_jobs(ssh_host: str, db_path: str) -> list[TrackerJob]:
    remote_python = f"""
import json
import sqlite3

db = sqlite3.connect({db_path!r})
db.row_factory = sqlite3.Row
rows = db.execute("select data from jobs").fetchall()
jobs = []
for row in rows:
    data = json.loads(row["data"])
    jobs.append({{
        "company": data.get("company", ""),
        "title": data.get("title", ""),
        "status": data.get("status", ""),
        "jobUrl": data.get("jobUrl", ""),
        "appliedDate": data.get("appliedDate", ""),
    }})
print(json.dumps(jobs))
""".strip()
    result = subprocess.run(
        ["ssh", ssh_host, "python3", "-"],
        check=True,
        capture_output=True,
        input=remote_python,
        text=True,
    )
    raw_jobs = json.loads(result.stdout)
    jobs: list[TrackerJob] = []
    for item in raw_jobs:
        if not isinstance(item, dict):
            continue
        jobs.append(
            TrackerJob(
                company=str(item.get("company", "")),
                title=str(item.get("title", "")),
                status=str(item.get("status", "")),
                job_url=str(item.get("jobUrl", "")),
                applied_date=str(item.get("appliedDate", "")),
            )
        )
    return jobs


def match_tracker_job(report_job: ReportJob, tracker_jobs: Iterable[TrackerJob]) -> TrackerJob | None:
    report_req = extract_requisition_id(report_job.url)
    report_url = canonicalize_url(report_job.url)
    report_company = normalize_text(report_job.company)
    report_title = normalize_text(report_job.title)

    fallback_match: TrackerJob | None = None
    for tracker_job in tracker_jobs:
        tracker_req = extract_requisition_id(tracker_job.job_url)
        if report_req and tracker_req:
            if report_req == tracker_req:
                return tracker_job
            continue

        tracker_url = canonicalize_url(tracker_job.job_url)
        if report_url and tracker_url and report_url == tracker_url:
            return tracker_job

        if (
            not report_req
            and not tracker_req
            and report_company
            and report_title
            and report_company == normalize_text(tracker_job.company)
            and report_title == normalize_text(tracker_job.title)
        ):
            fallback_match = tracker_job

    return fallback_match


def tracker_job_from_mapping(item: object, *, source_path: Path) -> TrackerJob:
    if not isinstance(item, dict):
        raise ValueError(f"Every tracker snapshot job must be an object in {source_path}")
    values: dict[str, str] = {}
    for key in ("company", "title", "status", "job_url", "applied_date"):
        value = item.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"Tracker snapshot field {key!r} must be a string in {source_path}")
        values[key] = value
    return TrackerJob(**values)


def write_tracker_jobs_snapshot(path: Path, *, jobs: Iterable[TrackerJob], ssh_host: str) -> Path:
    records = [
        {
            "company": job.company,
            "title": job.title,
            "status": job.status,
            "job_url": job.job_url,
            "applied_date": job.applied_date,
            "already_applied": job.already_applied,
        }
        for job in jobs
    ]
    payload = {
        "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
        "ssh_host": ssh_host,
        "jobs": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary_path.replace(path)
    return path


def load_tracker_jobs_snapshot(path: Path, *, expected_date: str | None = None) -> list[TrackerJob]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing applied-jobs snapshot: {path}")
    if expected_date is not None and path.name != f"{expected_date}-applied-jobs.json":
        raise ValueError(
            f"Applied-jobs snapshot {path.name!r} does not match workflow date {expected_date}"
        )
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError(f"Expected a top-level jobs list in applied-jobs snapshot: {path}")
    if not isinstance(payload.get("fetched_at"), str) or not payload["fetched_at"].strip():
        raise ValueError(f"Applied-jobs snapshot is missing fetched_at: {path}")
    if not isinstance(payload.get("ssh_host"), str) or not payload["ssh_host"].strip():
        raise ValueError(f"Applied-jobs snapshot is missing ssh_host: {path}")
    return [tracker_job_from_mapping(item, source_path=path) for item in payload["jobs"]]


def format_applied_value(tracker_job: TrackerJob | None) -> str:
    if not tracker_job or not tracker_job.already_applied:
        return "No"
    if tracker_job.applied_date:
        return f"Yes ({tracker_job.applied_date})"
    if tracker_job.status:
        return f"Yes ({tracker_job.status})"
    return "Yes"


def split_markdown_table_row(line: str) -> list[str]:
    raw_cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [cell.replace(r"\|", "|").strip() for cell in raw_cells]


def escape_markdown_table_cell(value: str) -> str:
    return value.replace("|", r"\|")


def parse_report_job(columns: list[str]) -> ReportJob:
    return ReportJob(
        company=columns[1],
        title=columns[2],
        posted=columns[3],
        location=columns[4],
        fit=columns[5],
        url=columns[6],
    )


def update_section(lines: list[str], section_name: str, tracker_jobs: list[TrackerJob]) -> list[str]:
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        output.append(line)
        if line.strip() != f"## {section_name}":
            index += 1
            continue

        header_index = index + 1
        while header_index < len(lines) and not lines[header_index].strip().startswith("|"):
            output.append(lines[header_index])
            header_index += 1
        if header_index >= len(lines):
            return output

        header_cells = split_markdown_table_row(lines[header_index])
        if "Already Applied" not in header_cells:
            header_cells.append("Already Applied")
        output.append("| " + " | ".join(escape_markdown_table_cell(cell) for cell in header_cells) + " |")
        output.append("|" + "---|" * len(header_cells))

        row_index = header_index + 2
        while row_index < len(lines):
            row = lines[row_index]
            stripped = row.strip()
            if not stripped or not stripped.startswith("|"):
                break
            columns = split_markdown_table_row(stripped)
            if len(columns) >= 7:
                report_job = parse_report_job(columns)
                tracker_match = match_tracker_job(report_job, tracker_jobs)
                applied_value = format_applied_value(tracker_match)
                columns = columns[:7] + [applied_value]
                output.append(
                    "| "
                    + " | ".join(escape_markdown_table_cell(column) for column in columns)
                    + " |"
                )
            else:
                output.append(row)
            row_index += 1

        index = row_index
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark daily-report jobs as already applied.")
    parser.add_argument("--daily-report", required=True, type=Path)
    parser.add_argument(
        "--ssh-host",
        default=os.environ.get("JOBTRACKER_SSH_HOST", DEFAULT_JOBTRACKER_SSH_HOST),
    )
    parser.add_argument(
        "--jobtracker-db-path",
        default=os.environ.get("JOBTRACKER_DB_PATH", "/var/lib/jobtracker/jobtracker.db"),
    )
    parser.add_argument("--applied-jobs-out", type=Path)
    parser.add_argument("--local-applied", type=Path, help="Candidate-maintained local applied-job snapshot; avoids SSH")
    args = parser.parse_args()

    tracker_jobs = (load_tracker_jobs_snapshot(args.local_applied) if args.local_applied else fetch_tracker_jobs(args.ssh_host, args.jobtracker_db_path))
    if args.local_applied:
        args.ssh_host = "local-candidate-record"
    lines = args.daily_report.read_text(encoding="utf-8").splitlines()
    updated = lines
    for section_name in (
        "Apply",
        "Maybe",
        "Discovery Candidates — Higher Signal",
        "Discovery Candidates — Review Signal",
    ):
        updated = update_section(updated, section_name, tracker_jobs)
    args.daily_report.write_text("\n".join(updated) + "\n", encoding="utf-8")
    if args.applied_jobs_out is not None:
        write_tracker_jobs_snapshot(args.applied_jobs_out, jobs=tracker_jobs, ssh_host=args.ssh_host)
    print(f"Updated daily report: {args.daily_report}")
    print(f"Fetched {len(tracker_jobs)} tracker jobs from {args.ssh_host}")
    if args.applied_jobs_out is not None:
        print(f"Wrote applied-jobs snapshot: {args.applied_jobs_out}")


if __name__ == "__main__":
    main()
