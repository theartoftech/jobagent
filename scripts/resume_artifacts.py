from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LEGACY_READY_HEADER = ("Priority", "Company", "Role", "Decision", "Template", "Fit", "URL")
READY_HEADER = (
    "Priority",
    "Company",
    "Role",
    "Decision",
    "Template",
    "Fit",
    "Overqualified Risk",
    "Resume Positioning",
    "URL",
)
AUTHORIZED_DECISIONS = {"STRONG APPLY", "APPLY", "MODERATE STRETCH"}
LEGACY_AUTHORIZED_DECISIONS = {"STRONG APPLY", "APPLY"}
AUTHORIZED_OVERQUALIFIED_RISKS = {"low", "medium", "high"}
AUTHORIZED_RESUME_POSITIONING = {
    "principal_architect_forward",
    "director_technical_leadership",
    "senior_lead_execution_forward",
}


@dataclass(frozen=True)
class ResumeReadyJob:
    priority: int
    company: str
    title: str
    decision: str
    template: str
    fit: int
    overqualified_risk: str
    resume_positioning: str
    url: str


@dataclass(frozen=True)
class ValidatedResumeArtifact:
    company: str
    title: str
    url: str
    template: str
    artifact_path: Path


def required_manifest_string(item: dict[str, Any], key: str, manifest_path: Path) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Resume production record in {manifest_path} requires {key!r}")
    return value.strip()


def resolve_manifest_artifact_path(manifest_path: Path, stored_path: str) -> Path:
    artifact_path = Path(stored_path)
    if artifact_path.is_absolute():
        return artifact_path
    if artifact_path.parts and artifact_path.parts[0] == manifest_path.parent.name:
        return manifest_path.parent.parent / artifact_path
    return manifest_path.parent / artifact_path


def load_validated_resume_artifacts(manifest_path: Path) -> list[ValidatedResumeArtifact]:
    payload: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a top-level object in resume production manifest: {manifest_path}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"Resume production manifest requires a records list: {manifest_path}")

    artifacts: list[ValidatedResumeArtifact] = []
    for item in records:
        if not isinstance(item, dict):
            raise ValueError(f"Resume production records must be objects: {manifest_path}")
        if item.get("validation_status") != "validated":
            continue
        stored_path = required_manifest_string(item, "artifact_path", manifest_path)
        artifacts.append(
            ValidatedResumeArtifact(
                company=required_manifest_string(item, "company", manifest_path),
                title=required_manifest_string(item, "title", manifest_path),
                url=required_manifest_string(item, "url", manifest_path),
                template=required_manifest_string(item, "template", manifest_path),
                artifact_path=resolve_manifest_artifact_path(manifest_path, stored_path),
            )
        )
    return artifacts


def split_markdown_row(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [cell.replace(r"\|", "|").strip() for cell in cells]


def run_date_from_ready_path(path: Path) -> str:
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})-resume-ready-jobs\.md", path.name)
    if match is None:
        raise ValueError("Resume-ready filename must be YYYY-MM-DD-resume-ready-jobs.md")
    return match.group(1)


def parse_resume_ready_jobs(path: Path) -> list[ResumeReadyJob]:
    if not path.exists():
        raise FileNotFoundError(f"Missing resume-ready queue: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if tuple(split_markdown_row(line)) in {READY_HEADER, LEGACY_READY_HEADER}
        ),
        None,
    )
    if header_index is None or header_index + 1 >= len(lines):
        raise ValueError(f"Missing required resume-ready table header in {path}")
    if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in split_markdown_row(lines[header_index + 1])):
        raise ValueError(f"Malformed resume-ready table separator in {path}")

    parsed_header = tuple(split_markdown_row(lines[header_index]))
    jobs: list[ResumeReadyJob] = []
    for line in lines[header_index + 2 :]:
        if not line.strip().startswith("|"):
            break
        cells = split_markdown_row(line)
        if tuple(cells) in {
            ("-", "-", "No jobs cleared strict resume workflow", "-", "-", "-", "-"),
            ("-", "-", "No jobs cleared strict resume workflow", "-", "-", "-", "-", "-", "-"),
        }:
            continue
        if len(cells) != len(parsed_header):
            raise ValueError(f"Expected {len(parsed_header)} columns in resume-ready row; found {len(cells)}")
        if parsed_header == READY_HEADER:
            (
                priority_text,
                company,
                title,
                decision,
                template,
                fit_text,
                overqualified_risk,
                resume_positioning,
                url,
            ) = cells
            if overqualified_risk not in AUTHORIZED_OVERQUALIFIED_RISKS:
                raise ValueError(
                    f"Resume-ready row has unauthorized overqualified risk: {company} — {title} ({overqualified_risk})"
                )
            if resume_positioning not in AUTHORIZED_RESUME_POSITIONING:
                raise ValueError(
                    f"Resume-ready row has invalid resume positioning: {company} — {title} ({resume_positioning})"
                )
        else:
            priority_text, company, title, decision, template, fit_text, url = cells
            overqualified_risk = "not_assessed_legacy"
            resume_positioning = "not_assessed_legacy"
        allowed_decisions = (
            AUTHORIZED_DECISIONS if parsed_header == READY_HEADER else LEGACY_AUTHORIZED_DECISIONS
        )
        if decision not in allowed_decisions:
            raise ValueError(f"Resume-ready row is not authorized for production: {company} — {title} ({decision})")
        if not company or not title or not template:
            raise ValueError("Resume-ready rows require company, role, and template values")
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"Resume-ready row has an invalid job URL: {url}")
        try:
            priority = int(priority_text)
            fit = int(fit_text)
        except ValueError as exc:
            raise ValueError(f"Resume-ready priority and fit must be integers: {line}") from exc
        jobs.append(
            ResumeReadyJob(
                priority=priority,
                company=company,
                title=title,
                decision=decision,
                template=template,
                fit=fit,
                overqualified_risk=overqualified_risk,
                resume_positioning=resume_positioning,
                url=url,
            )
        )

    artifact_names = resume_artifact_names(run_date_from_ready_path(path), jobs)
    if len(set(artifact_names.values())) != len(artifact_names):
        raise ValueError("Resume-ready queue contains duplicate posting URLs")
    return jobs


def artifact_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")


def resume_artifact_names(
    run_date: str,
    jobs: list[ResumeReadyJob],
    *,
    candidate_name: str = "Candidate Name",
) -> dict[ResumeReadyJob, str]:
    """Build distinct artifact names, preserving legacy names when a title is unique."""
    base_names = [
        resume_artifact_name(run_date, job.company, job.title, candidate_name=candidate_name)
        for job in jobs
    ]
    duplicate_bases = {name for name in base_names if base_names.count(name) > 1}
    return {
        job: resume_artifact_name(
            run_date,
            job.company,
            job.title,
            disambiguator_url=job.url if base_name in duplicate_bases else "",
            candidate_name=candidate_name,
        )
        for job, base_name in zip(jobs, base_names, strict=True)
    }


def resume_artifact_name(
    run_date: str,
    company: str,
    title: str,
    *,
    disambiguator_url: str = "",
    candidate_name: str = "Candidate Name",
) -> str:
    company_slug = artifact_slug(company)
    title_slug = artifact_slug(title)
    if not company_slug or not title_slug:
        raise ValueError("Resume artifact names require a company and title")
    suffix = ""
    if disambiguator_url:
        url_hash = hashlib.sha256(disambiguator_url.encode("utf-8")).hexdigest()[:10]
        suffix = f"-posting-{url_hash}"
    candidate_slug = artifact_slug(candidate_name)
    if not candidate_slug:
        raise ValueError("Resume artifact names require a candidate name")
    return f"{run_date}-{company_slug}-{title_slug}{suffix}-{candidate_slug}.docx"
