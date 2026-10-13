#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse

if __package__:
    from scripts.linkedin_connection_notes import (
        LINKEDIN_CONNECTION_NOTE_MAX_CHARS,
        build_linkedin_connection_note,
    )
    from scripts.render_daily_job_search_html import NetworkingCompanySection, parse_networking_markdown
    from scripts.mark_applied_jobs import extract_requisition_id
    from scripts.resume_artifacts import (
        load_validated_resume_artifacts,
        resolve_manifest_artifact_path,
        resume_artifact_name,
    )
else:
    from linkedin_connection_notes import LINKEDIN_CONNECTION_NOTE_MAX_CHARS, build_linkedin_connection_note
    from render_daily_job_search_html import NetworkingCompanySection, parse_networking_markdown
    from mark_applied_jobs import extract_requisition_id
    from resume_artifacts import load_validated_resume_artifacts, resolve_manifest_artifact_path, resume_artifact_name


DECISION_ORDER = (
    "STRONG APPLY",
    "APPLY",
    "MODERATE STRETCH",
    "EXTREME STRETCH",
    "SKIP",
)


@dataclass(frozen=True)
class ReviewRecord:
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
    already_applied: bool
    applied_status: str
    applied_date: str
    hard_blockers: list[str]
    required_gaps: list[str]
    preferred_gaps: list[str]
    resolved_gaps: dict[str, str]
    direct_matches: list[str]
    questions_for_candidate: list[str]
    final_action: str
    risk_flags: list[str]
    seniority_scope_risks: list[str]
    interview_positioning_note: str


@dataclass(frozen=True)
class HumanSearchCompany:
    name: str
    careers_url: str


def string_value(value: object) -> str:
    return str(value or "").strip()


def string_list(item: dict[str, Any], key: str) -> list[str]:
    value = item.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"Expected '{key}' to be a list for {item.get('company', 'unknown company')}")
    return [string_value(entry) for entry in value if string_value(entry)]


def string_mapping(item: dict[str, Any], key: str) -> dict[str, str]:
    value = item.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"Expected '{key}' to be an object for {item.get('company', 'unknown company')}")
    return {
        string_value(map_key): string_value(map_value)
        for map_key, map_value in value.items()
        if string_value(map_key) and string_value(map_value)
    }


def integer_value(item: dict[str, Any], key: str) -> int:
    value = item.get(key, 0)
    if isinstance(value, bool):
        raise ValueError(f"Expected '{key}' to be an integer, not boolean")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected '{key}' to be an integer") from exc


def boolean_value(item: dict[str, Any], key: str) -> bool:
    value = item.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"Expected '{key}' to be a boolean")
    return value


def parse_review(item: dict[str, Any]) -> ReviewRecord:
    url = string_value(item.get("url"))
    return ReviewRecord(
        company=string_value(item.get("company")),
        title=string_value(item.get("title")),
        job_id=string_value(item.get("job_id")) or extract_requisition_id(url),
        url=url,
        location=string_value(item.get("location")),
        work_model=string_value(item.get("work_model")),
        source_decision=string_value(item.get("source_decision")),
        source_fit_score=integer_value(item, "source_fit_score"),
        workflow_decision=string_value(item.get("workflow_decision")) or "UNKNOWN",
        should_generate_resume=boolean_value(item, "should_generate_resume"),
        recommended_resume_base=string_value(item.get("recommended_resume_base")),
        overqualified_risk=string_value(item.get("overqualified_risk")) or "not assessed",
        resume_positioning=string_value(item.get("resume_positioning")) or "not assessed",
        gap_questions_needed=boolean_value(item, "gap_questions_needed"),
        already_applied=boolean_value(item, "already_applied"),
        applied_status=string_value(item.get("applied_status")),
        applied_date=string_value(item.get("applied_date")),
        hard_blockers=string_list(item, "hard_blockers"),
        required_gaps=string_list(item, "required_gaps"),
        preferred_gaps=string_list(item, "preferred_gaps"),
        resolved_gaps=string_mapping(item, "resolved_gaps"),
        direct_matches=string_list(item, "direct_matches"),
        questions_for_candidate=string_list(item, "questions_for_candidate"),
        final_action=string_value(item.get("final_action")),
        risk_flags=string_list(item, "risk_flags"),
        seniority_scope_risks=string_list(item, "seniority_scope_risks"),
        interview_positioning_note=string_value(item.get("interview_positioning_note")),
    )


def load_workflow_review(path: Path) -> tuple[str, list[ReviewRecord]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing workflow review JSON: {path}")
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a top-level JSON object in {path}")

    generated_on = string_value(payload.get("generated_on"))
    if not generated_on:
        raise ValueError(f"Missing 'generated_on' in {path}")

    raw_reviews = payload.get("reviews")
    if not isinstance(raw_reviews, list):
        raise ValueError(f"Expected a top-level 'reviews' list in {path}")
    if any(not isinstance(item, dict) for item in raw_reviews):
        raise ValueError(f"Every review must be a JSON object in {path}")

    reviews = [parse_review(item) for item in raw_reviews if isinstance(item, dict)]
    return generated_on, reviews


def load_human_search_companies(path: Path) -> list[HumanSearchCompany]:
    if not path.exists():
        return []
    companies: list[HumanSearchCompany] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"\s*\d+\.\s+(.+?)\s+-\s+(https?://\S+)\s*", line)
        if match is not None:
            companies.append(
                HumanSearchCompany(name=match.group(1).strip(), careers_url=match.group(2).strip())
            )
    if not companies:
        raise ValueError(f"Human-search company file contains no numbered company URLs: {path}")
    return companies


def escape(value: object) -> str:
    return html.escape(string_value(value), quote=True)


def decision_class(decision: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", decision.casefold()).strip("-")
    return normalized or "unknown"


def job_link(record: ReviewRecord) -> str:
    parsed = urlparse(record.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return '<span class="muted">Job link unavailable</span>'
    return f'<a class="job-link" href="{escape(record.url)}" target="_blank" rel="noopener noreferrer">Open job posting</a>'


def render_items(title: str, items: list[str], *, tone: str = "neutral") -> str:
    if not items:
        return ""
    rendered_items = "".join(f"<li>{escape(item)}</li>" for item in items)
    return (
        f'<div class="detail-list {escape(tone)}">'
        f"<h4>{escape(title)}</h4>"
        f"<ul>{rendered_items}</ul>"
        "</div>"
    )


def resolution_prompt(record: ReviewRecord) -> str:
    questions = "\n".join(f"- {question}" for question in record.questions_for_candidate)
    return (
        f"Resolve workflow review for {record.company} — {record.title}\n"
        f"Posting: {record.url or 'Unavailable'}\n"
        f"Current decision: {record.workflow_decision}\n"
        f"Overqualified risk: {record.overqualified_risk}\n"
        f"Resume positioning: {record.resume_positioning}\n"
        f"Recommended resume base: {record.recommended_resume_base or 'none'}\n\n"
        "For each gap question, answer with truthful professional evidence, limited exposure, "
        "do not claim, or posting false positive.\n\n"
        f"Questions:\n{questions}\n\n"
        "Save candidate-wide skill facts under candidate_gap_resolutions so they are answered once. "
        "Use the exact posting URL under gap_resolutions only for posting-specific interpretations.\n\n"
        "Then re-run scripts/review_job_fit.py and confirm this job appears in "
        "outputs/YYYY-MM-DD-resume-ready-jobs.md before generating a resume or cover letter."
    )


def render_resolution_controls(record: ReviewRecord) -> str:
    if not record.questions_for_candidate or record.workflow_decision not in {
        "STRONG APPLY",
        "APPLY",
        "MODERATE STRETCH",
    }:
        return ""
    prompt = escape(resolution_prompt(record))
    return (
        '<div class="resolution-controls">'
        f'<button type="button" class="copy-resolution" data-resolution-prompt="{prompt}">Copy Resolution Prompt</button>'
        '<span class="copy-status" role="status" aria-live="polite"></span>'
        "</div>"
    )


def batch_resolution_prompt(records: list[ReviewRecord], generated_on: str) -> str:
    if not records:
        raise ValueError("Batch resolution prompt requires at least one actionable gap job")

    job_blocks: list[str] = []
    for index, record in enumerate(records, start=1):
        questions = "\n".join(f"- {question}" for question in record.questions_for_candidate)
        job_blocks.append(
            f"Job {index}: {record.company} — {record.title}\n"
            f"Posting: {record.url or 'Unavailable'}\n"
            f"Current decision: {record.workflow_decision}\n"
            f"Overqualified risk: {record.overqualified_risk}\n"
            f"Resume positioning: {record.resume_positioning}\n"
            f"Recommended resume base: {record.recommended_resume_base or 'none'}\n"
            f"Questions:\n{questions}"
        )

    return (
        f"Batch workflow gap resolution — {generated_on}\n\n"
        "Work through every job below as one resolution batch. First present a compact answer sheet and "
        "wait for the candidate's truthful answers; do not infer or fabricate professional evidence. For every question, "
        "record one of: professional_evidence: <evidence>, limited_exposure: <context>, do_not_claim, or "
        "posting_false_positive: <reason>. Unanswered questions must remain unresolved.\n\n"
        + "\n\n".join(job_blocks)
        + "\n\nAfter the candidate answers:\n"
        "1. Validate every answer. Save candidate-wide skill facts in candidate_workflow_profile.yml under "
        "candidate_gap_resolutions so the candidate answers each factual experience question once. Save only "
        "posting-specific interpretations under gap_resolutions, keyed by the exact posting URL. "
        "Never store posting_false_positive as a candidate-wide answer.\n"
        f"2. Re-run scripts/review_job_fit.py for {generated_on} using outputs/{generated_on}-discovered-jobs.json "
        f"and outputs/{generated_on}-applied-jobs.json.\n"
        "3. Report separately which jobs moved to Resume Ready, which still need answers, and which were "
        "downgraded or rejected. Do not manually mark a job Resume Ready; let the strict reviewer decide.\n"
        f"4. Use outputs/{generated_on}-resume-ready-jobs.md as the sole authorization queue and run "
        f"scripts/produce_resumes.py only after the reviewer succeeds.\n"
        "5. Reuse already validated resumes for exact posting matches, generate only missing authorized resumes, "
        "validate every artifact, and regenerate the workflow HTML with working resume links.\n"
        "6. Do not generate a resume or cover letter for a job absent from the refreshed resume-ready file."
    )


def render_batch_resolution_controls(records: list[ReviewRecord], generated_on: str) -> str:
    if not records:
        return ""
    prompt = escape(batch_resolution_prompt(records, generated_on))
    job_label = "job" if len(records) == 1 else "jobs"
    return (
        '<div class="batch-resolution">'
        '<div><h3>Resolve the entire gap queue</h3>'
        '<p>Copy one prompt, answer every job together, then let the strict reviewer decide what becomes Resume Ready.</p></div>'
        '<div class="resolution-controls">'
        f'<button type="button" class="copy-resolution copy-all-resolution" data-resolution-prompt="{prompt}">'
        f"Copy All Gap Questions ({len(records)} {job_label})</button>"
        '<span class="copy-status" role="status" aria-live="polite"></span>'
        "</div></div>"
    )


def resume_artifact_link(
    *,
    generated_on: str,
    record: ReviewRecord,
    resume_dir: Path,
    produced_artifacts: Mapping[tuple[str, str], Path],
    link_from_dir: Path | None = None,
) -> str:
    artifact_path = produced_artifacts.get((record.company.casefold(), record.url))
    if artifact_path is None:
        artifact_name = resume_artifact_name(generated_on, record.company, record.title)
        artifact_path = resume_dir / artifact_name
    if not artifact_path.is_file() or artifact_path.stat().st_size == 0:
        return '<span class="resume-missing">Resume not generated</span>'
    relative_path = Path(os.path.relpath(artifact_path, start=link_from_dir or resume_dir.parent))
    href = quote(relative_path.as_posix(), safe="/")
    return f'<a class="resume-link" href="{escape(href)}">Open generated resume</a>'


def load_validated_cover_letter_artifacts(manifest_path: Path) -> dict[tuple[str, str], Path]:
    payload: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError(f"Expected a records list in cover-letter production manifest: {manifest_path}")
    artifacts: dict[tuple[str, str], Path] = {}
    for item in payload["records"]:
        if not isinstance(item, dict):
            raise ValueError(f"Cover-letter production records must be objects: {manifest_path}")
        if item.get("validation_status") != "validated":
            continue
        company = string_value(item.get("company"))
        url = string_value(item.get("url"))
        artifact_path = string_value(item.get("artifact_path"))
        if not company or not url or not artifact_path:
            raise ValueError(f"Validated cover-letter record is incomplete: {manifest_path}")
        key = (company.casefold(), url)
        if key in artifacts:
            raise ValueError(f"Duplicate job URL in cover-letter production manifest: {url}")
        artifacts[key] = resolve_manifest_artifact_path(manifest_path, artifact_path)
    return artifacts


def cover_letter_artifact_link(
    *,
    record: ReviewRecord,
    produced_artifacts: Mapping[tuple[str, str], Path],
    link_from_dir: Path | None = None,
) -> str:
    artifact_path = produced_artifacts.get((record.company.casefold(), record.url))
    if artifact_path is None or not artifact_path.is_file() or artifact_path.stat().st_size == 0:
        return '<span class="resume-missing">Cover letter not generated</span>'
    relative_path = Path(os.path.relpath(artifact_path, start=link_from_dir or artifact_path.parents[1]))
    href = quote(relative_path.as_posix(), safe="/")
    return f'<a class="resume-link" href="{escape(href)}">Open cover letter</a>'


def render_resume_ready_checkbox(record: ReviewRecord) -> str:
    label = f"Mark {record.company} — {record.title} as applied"
    return (
        '<label class="resume-ready-check-label">'
        f'<input class="resume-ready-check" type="checkbox" data-job-url="{escape(record.url)}" '
        f'aria-label="{escape(label)}" autocomplete="off">'
        "Applied"
        "</label>"
    )


def render_compact_details(*, label: str, content: str, empty_text: str = "None") -> str:
    normalized_content = content.strip()
    if not normalized_content:
        return escape(empty_text)
    return (
        '<details class="compact-details">'
        f"<summary>{escape(label)}</summary>"
        f"<p>{escape(normalized_content)}</p>"
        "</details>"
    )


def detail_page_filename(record: ReviewRecord) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{record.company}-{record.title}".casefold()).strip("-")
    identity_hash = hashlib.sha256(record.url.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:100]}-{identity_hash}.html"


def detail_page_href(generated_on: str, record: ReviewRecord) -> str:
    return f"{generated_on}-workflow-review-details/{detail_page_filename(record)}"


def render_ready_table(
    records: list[ReviewRecord],
    *,
    generated_on: str,
) -> str:
    if not records:
        return '<div class="empty-state">No jobs cleared the strict resume workflow.</div>'

    rows: list[str] = []
    for priority, record in enumerate(records, start=1):
        detail_href = detail_page_href(generated_on, record)
        rows.append(
            f'<tr class="clickable-job-row" tabindex="0" data-detail-href="{escape(detail_href)}" '
            f'aria-label="Open details for {escape(record.company)} — {escape(record.title)}">'
            f"<td>{priority}</td>"
            f"<td><strong>{escape(record.company)}</strong></td>"
            f'<td><a class="detail-link" href="{escape(detail_href)}">{escape(record.title)}</a></td>'
            f'<td><span class="decision {decision_class(record.workflow_decision)}">{escape(record.workflow_decision)}</span></td>'
            f"<td>{escape(record.overqualified_risk)}</td>"
            f"<td>{record.source_fit_score}</td>"
            f"<td>{escape(record.location or 'Unknown')} / {escape(record.work_model or 'Unknown')}</td>"
            f'<td><a class="detail-link" href="{escape(detail_href)}">Open role details</a></td>'
            f"<td>{render_resume_ready_checkbox(record)}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Priority</th><th>Company</th><th>Role</th><th>Decision</th>"
        "<th>Overqualified risk</th><th>Fit</th><th>Location / model</th><th>Details</th><th>Application</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def render_review_card(record: ReviewRecord) -> str:
    location = f"{record.location or 'Unknown'} / {record.work_model or 'Unknown'}"
    details = [
        render_items("Direct matches", record.direct_matches, tone="positive"),
        render_items("Required gaps", record.required_gaps, tone="danger"),
        render_items("Preferred / positioning gaps", record.preferred_gaps, tone="warning"),
        render_items("Hard blockers", record.hard_blockers, tone="danger"),
        render_items("Questions for Candidate", record.questions_for_candidate, tone="warning"),
        render_items("Risk flags", record.risk_flags, tone="warning"),
        render_items("Seniority / scope risks", record.seniority_scope_risks, tone="warning"),
        render_items(
            "Interview positioning",
            [record.interview_positioning_note] if record.interview_positioning_note else [],
            tone="warning",
        ),
    ]
    return (
        '<article class="review-card">'
        '<div class="review-card-heading">'
        "<div>"
        f"<h3>{escape(record.company)} — {escape(record.title)}</h3>"
        f'<p class="meta">Discovery candidate · Original score {record.source_fit_score} · {escape(location)}</p>'
        "</div>"
        f'<span class="decision {decision_class(record.workflow_decision)}">{escape(record.workflow_decision)}</span>'
        "</div>"
        f'<p class="action"><strong>Action:</strong> {escape(record.final_action or "No action supplied.")}</p>'
        f'<div class="detail-grid">{"".join(detail for detail in details if detail)}</div>'
        f'{render_resolution_controls(record)}'
        f'<div class="card-footer"><span>Resume base: {escape(record.recommended_resume_base or "none")} · '
        f'Positioning: {escape(record.resume_positioning)} · Overqualified risk: {escape(record.overqualified_risk)}</span>{job_link(record)}</div>'
        "</article>"
    )


def render_review_groups(records: list[ReviewRecord]) -> str:
    if not records:
        return '<div class="empty-state">No jobs require human review.</div>'
    sections: list[str] = []
    known_decisions = list(DECISION_ORDER)
    known_decisions.extend(
        sorted({record.workflow_decision for record in records if record.workflow_decision not in DECISION_ORDER})
    )
    for decision in known_decisions:
        group = [record for record in records if record.workflow_decision == decision]
        if not group:
            continue
        group.sort(key=lambda record: (-record.source_fit_score, record.company, record.title))
        sections.append(
            f'<section class="decision-group"><h2>{escape(decision)} <span class="count">{len(group)}</span></h2>'
            + "".join(render_review_card(record) for record in group)
            + "</section>"
        )
    return "".join(sections)


def render_applied_table(records: list[ReviewRecord]) -> str:
    if not records:
        return '<div class="empty-state">No reviewed jobs are marked as already applied.</div>'
    rows = "".join(
        "<tr>"
        f"<td><strong>{escape(record.company)}</strong></td>"
        f"<td>{escape(record.title)}</td>"
        f"<td>{escape(record.applied_date or record.applied_status or 'Applied')}</td>"
        f"<td>{job_link(record)}</td>"
        "</tr>"
        for record in sorted(records, key=lambda item: (item.company, item.title))
    )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        '<th>Company</th><th>Role</th><th>Applied</th><th>Posting</th>'
        "</tr></thead><tbody>"
        + rows
        + "</tbody></table></div>"
    )


def render_detail_list(items: list[str], *, empty_text: str = "None") -> str:
    if not items:
        return f'<p class="empty-copy">{escape(empty_text)}</p>'
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def normalize_company_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def load_networking_sections(path: Path) -> dict[str, NetworkingCompanySection]:
    if not path.exists():
        return {}
    sections, _ = parse_networking_markdown(path.read_text(encoding="utf-8"))
    return {normalize_company_key(company): section for company, section in sections.items()}


def recruiter_message(record: ReviewRecord, contact_name: str = "") -> str:
    return build_linkedin_connection_note(
        company=record.company,
        role=record.title,
        contact_name=contact_name,
    )


def render_linkedin_outreach(
    record: ReviewRecord,
    networking: NetworkingCompanySection | None,
) -> str:
    contacts = networking.contacts[:8] if networking is not None else []
    if contacts:
        rows: list[str] = []
        for contact in contacts:
            parsed = urlparse(contact.linkedin_url)
            profile = (
                f'<a href="{escape(contact.linkedin_url)}" target="_blank" '
                'rel="noopener noreferrer">Open profile</a>'
                if parsed.scheme in {"http", "https"} and bool(parsed.netloc)
                else '<span class="empty-copy">Profile unavailable</span>'
            )
            rows.append(
                "<tr>"
                f"<td><strong>{escape(contact.name)}</strong></td>"
                f"<td>{escape(contact.current_role or 'Role unavailable')}</td>"
                f"<td>{escape(contact.current_company or record.company)}</td>"
                f"<td>{escape(contact.match_source or 'networking report')}</td>"
                f"<td>{escape(contact.why_reach_out or 'Current-company contact')}</td>"
                f"<td>{profile}</td>"
                "</tr>"
            )
        contacts_html = (
            "<h3>Known contacts</h3>"
            '<div class="contact-table-wrap"><table class="contact-table"><thead><tr>'
            "<th>Name</th><th>Role</th><th>Company</th><th>Confirmation</th>"
            "<th>Why reach out</th><th>LinkedIn</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )
        message_name = contacts[0].name
        message_heading = f"Suggested message for {message_name}"
    else:
        contacts_html = (
            '<p class="outreach-notice">No matching current-company LinkedIn contacts were found '
            "in the latest networking report. Use the introductory message with a recruiter or "
            "hiring manager you find on LinkedIn.</p>"
        )
        message_name = ""
        message_heading = "Suggested LinkedIn connection note"

    message = recruiter_message(record, message_name)

    return (
        '<section class="section wide linkedin-outreach"><h2>LinkedIn outreach</h2>'
        f"{contacts_html}<h3>{escape(message_heading)}</h3>"
        '<div class="outreach-message">'
        f'<div class="message-text">{escape(message)}</div>'
        '<div class="message-actions"><button class="copy-outreach" type="button">Copy message</button>'
        f'<span class="message-count">{len(message)} / {LINKEDIN_CONNECTION_NOTE_MAX_CHARS} characters</span>'
        '<span class="copy-status" role="status" aria-live="polite"></span></div>'
        "</div></section>"
    )


def render_workflow_detail_document(
    *,
    generated_on: str,
    record: ReviewRecord,
    resume_dir: Path,
    detail_dir: Path,
    produced_artifacts: Mapping[tuple[str, str], Path],
    produced_cover_letters: Mapping[tuple[str, str], Path],
    networking: NetworkingCompanySection | None,
) -> str:
    resolved_gap_items = [f"{gap}: {answer}" for gap, answer in record.resolved_gaps.items()]
    location = f"{record.location or 'Unknown'} / {record.work_model or 'Unknown'}"
    resume_link = resume_artifact_link(
        generated_on=generated_on,
        record=record,
        resume_dir=resume_dir,
        produced_artifacts=produced_artifacts,
        link_from_dir=detail_dir,
    )
    cover_letter_link = cover_letter_artifact_link(
        record=record,
        produced_artifacts=produced_cover_letters,
        link_from_dir=detail_dir,
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(record.company)} - {escape(record.title)} - Workflow Review</title>
<style>
:root {{ color-scheme:light; --ink:#172033; --muted:#667085; --line:#dce2ea; --canvas:#f4f7fb; --panel:#fff; --navy:#173b63; --green:#137a4b; --amber:#a55b08; --red:#b42318; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--canvas); color:var(--ink); font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
main {{ width:min(1320px,calc(100% - 32px)); margin:0 auto 64px; }}
.back-link {{ display:inline-block; margin:22px 0 12px; color:#0b63a5; font-weight:750; text-decoration:none; }}
.back-link:hover {{ text-decoration:underline; }}
.hero {{ padding:26px 0 22px; border-bottom:1px solid var(--line); }}
.eyebrow {{ margin:0 0 5px; color:var(--navy); font-weight:800; }}
h1 {{ margin:0; font-size:clamp(27px,4vw,42px); line-height:1.15; letter-spacing:0; }}
.badges {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }}
.badge {{ padding:6px 10px; border-radius:999px; background:#e9eef5; font-size:12px; font-weight:800; }}
.badge.apply {{ color:#08633d; background:#d9f2e5; }}
.badge.risk-medium,.badge.risk-high {{ color:#8a4b05; background:#fff0d5; }}
.summary-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:1px; margin:22px 0; border:1px solid var(--line); background:var(--line); }}
.summary-item {{ min-width:0; padding:16px; background:var(--panel); }}
.summary-item dt {{ color:var(--muted); font-size:12px; font-weight:800; text-transform:uppercase; }}
.summary-item dd {{ margin:5px 0 0; overflow-wrap:anywhere; font-weight:700; }}
.actions {{ display:flex; flex-wrap:wrap; gap:10px; padding:16px 0 24px; }}
.actions .job-link,.actions .resume-link,.action-disabled {{ display:inline-flex; align-items:center; min-height:40px; padding:9px 13px; border:1px solid #0b63a5; border-radius:7px; font-weight:750; text-decoration:none; }}
.actions .job-link,.actions .resume-link {{ color:#fff; background:#0b63a5; }}
.actions .job-link:hover,.actions .resume-link:hover {{ background:#084f83; }}
.action-disabled,.resume-missing {{ color:var(--muted); border-color:var(--line); background:#fff; }}
.content-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }}
.section {{ padding:20px; border:1px solid var(--line); background:var(--panel); }}
.section.wide {{ grid-column:1 / -1; }}
h2 {{ margin:0 0 12px; color:var(--navy); font-size:20px; }}
h3 {{ margin:18px 0 8px; font-size:15px; }}
ul {{ margin:0; padding-left:20px; }}
li + li {{ margin-top:5px; }}
.positioning {{ margin:12px 0 0; padding:15px; border-left:4px solid var(--amber); background:#fff8e8; white-space:pre-line; }}
.final-action {{ padding:15px; border-left:4px solid var(--green); background:#edf8f2; font-weight:700; }}
.empty-copy {{ margin:0; color:var(--muted); }}
.outreach-notice {{ margin:0 0 18px; padding:13px 15px; border-left:4px solid #0b63a5; background:#edf6fc; }}
.contact-table-wrap {{ overflow-x:auto; }}
.contact-table {{ width:100%; min-width:900px; border-collapse:collapse; }}
.contact-table th,.contact-table td {{ padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
.contact-table th {{ color:#344054; background:#edf3f8; font-size:12px; text-transform:uppercase; }}
.contact-table a {{ color:#0b63a5; font-weight:750; }}
.outreach-message {{ padding:16px; border:1px solid #b9d8e8; background:#f7fbfe; }}
.message-text {{ white-space:pre-wrap; }}
.message-actions {{ display:flex; align-items:center; gap:12px; margin-top:14px; }}
.copy-outreach {{ min-height:38px; padding:8px 12px; border:1px solid #0b63a5; border-radius:6px; color:#fff; background:#0b63a5; font:inherit; font-weight:750; cursor:pointer; }}
.copy-outreach:hover {{ background:#084f83; }}
.copy-status {{ color:var(--muted); font-size:13px; }}
.message-count {{ color:var(--muted); font-size:13px; }}
.job-link,.resume-link {{ color:#0b63a5; font-weight:750; }}
@media (max-width:850px) {{ .summary-grid,.content-grid {{ grid-template-columns:1fr; }} .section.wide {{ grid-column:auto; }} main {{ width:min(100% - 20px,1320px); }} }}
</style>
</head>
<body>
<main>
<a class="back-link" href="../{escape(generated_on)}-workflow-review.html">Back to Workflow Review</a>
<header class="hero">
  <p class="eyebrow">{escape(record.company)}</p>
  <h1>{escape(record.title)}</h1>
  <div class="badges">
    <span class="badge {decision_class(record.workflow_decision)}">{escape(record.workflow_decision)}</span>
    <span class="badge risk-{escape(record.overqualified_risk)}">Overqualified risk: {escape(record.overqualified_risk)}</span>
    <span class="badge">Fit: {record.source_fit_score}</span>
  </div>
</header>
<dl class="summary-grid">
  <div class="summary-item"><dt>Job ID</dt><dd>{escape(record.job_id or 'Not provided')}</dd></div>
  <div class="summary-item"><dt>Location / work model</dt><dd>{escape(location)}</dd></div>
  <div class="summary-item"><dt>Resume base</dt><dd>{escape(record.recommended_resume_base or 'none')}</dd></div>
  <div class="summary-item"><dt>Resume positioning</dt><dd>{escape(record.resume_positioning)}</dd></div>
  <div class="summary-item"><dt>Run resume workflow</dt><dd>{'YES' if record.should_generate_resume else 'NO'}</dd></div>
  <div class="summary-item"><dt>Source decision</dt><dd>{escape(record.source_decision or 'Unknown')}</dd></div>
  <div class="summary-item"><dt>Already applied</dt><dd>{'YES' if record.already_applied else 'NO'}</dd></div>
  <div class="summary-item"><dt>Gap questions needed</dt><dd>{'YES' if record.gap_questions_needed else 'NO'}</dd></div>
  <div class="summary-item"><dt>Review snapshot</dt><dd>{escape(generated_on)}</dd></div>
</dl>
<nav class="actions" aria-label="Role actions">
  {job_link(record)}
  {resume_link}
  {cover_letter_link}
</nav>
<div class="content-grid">
  <section class="section"><h2>Why it fits</h2>{render_detail_list(record.direct_matches, empty_text='No direct matches recorded.')}</section>
  <section class="section"><h2>Technical gaps</h2><h3>Required</h3>{render_detail_list(record.required_gaps)}<h3>Preferred / positioning</h3>{render_detail_list(record.preferred_gaps)}<h3>Resolved answers</h3>{render_detail_list(resolved_gap_items)}</section>
  <section class="section wide"><h2>Seniority / scope risks</h2>{render_detail_list(record.seniority_scope_risks)}<h3>Interview positioning</h3><div class="positioning">{escape(record.interview_positioning_note or 'Use standard technical interview positioning.')}</div></section>
  <section class="section"><h2>Other review signals</h2><h3>Risk flags</h3>{render_detail_list(record.risk_flags)}<h3>Hard blockers</h3>{render_detail_list(record.hard_blockers)}</section>
  <section class="section"><h2>Questions for Candidate</h2>{render_detail_list(record.questions_for_candidate, empty_text='No unresolved questions.')}</section>
  <section class="section wide"><h2>Recommended action</h2><div class="final-action">{escape(record.final_action or 'No action supplied.')}</div></section>
  {render_linkedin_outreach(record, networking)}
</div>
</main>
<script>
document.addEventListener("click", async (event) => {{
  const target = event.target;
  if (!(target instanceof HTMLButtonElement) || !target.classList.contains("copy-outreach")) return;
  const container = target.closest(".outreach-message");
  const message = container?.querySelector(".message-text")?.textContent?.trim();
  const status = container?.querySelector(".copy-status");
  if (!message || !(status instanceof HTMLElement)) throw new Error("Outreach message markup is incomplete.");
  try {{
    await navigator.clipboard.writeText(message);
    status.textContent = "Copied.";
  }} catch (error) {{
    status.textContent = "Copy failed. Select the message and copy it manually.";
  }}
}});
</script>
</body>
</html>
"""
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Company</th><th>Role</th><th>Applied status</th><th>Posting</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def render_company_checklist(companies: list[HumanSearchCompany]) -> str:
    if not companies:
        return '<div class="empty-state">No companies were selected for manual checking today.</div>'
    items: list[str] = []
    for index, company in enumerate(companies, start=1):
        checkbox_id = f"company-check-{index}"
        items.append(
            "<li>"
            f'<input class="company-check" type="checkbox" id="{checkbox_id}" '
            f'data-company="{escape(company.name)}">'
            "<div>"
            f'<label class="company-name" for="{checkbox_id}">{escape(company.name)}</label>'
            f'<a class="company-url" href="{escape(company.careers_url)}" target="_blank" '
            'rel="noopener noreferrer">Open company careers</a>'
            "</div>"
            "</li>"
        )
    return (
        '<div class="checklist-header"><div><h2>Companies to Check</h2>'
        f'<p><strong id="checklist-progress">0 of {len(companies)} searched</strong></p></div>'
        '<button class="reset-checklist" type="button">Reset checklist</button></div>'
        f'<ul class="company-checklist">{"".join(items)}</ul>'
        '<p class="storage-status" role="status" aria-live="polite"></p>'
    )


def render_document(
    *,
    generated_on: str,
    reviews: list[ReviewRecord],
    resume_dir: Path,
    produced_artifacts: Mapping[tuple[str, str], Path],
    produced_cover_letters: Mapping[tuple[str, str], Path],
    human_search_companies: list[HumanSearchCompany],
) -> str:
    ready = sorted(
        (record for record in reviews if record.should_generate_resume and not record.already_applied),
        key=lambda record: (-record.source_fit_score, record.company, record.title),
    )
    gap_questions = sorted(
        (
            record
            for record in reviews
            if not record.already_applied
            and not record.should_generate_resume
            and record.workflow_decision in {"STRONG APPLY", "APPLY", "MODERATE STRETCH"}
            and bool(record.questions_for_candidate)
        ),
        key=lambda record: (-record.source_fit_score, record.company, record.title),
    )
    applied = [record for record in reviews if record.already_applied]
    actionable_ids = {id(record) for record in [*ready, *gap_questions, *applied]}
    diagnostics = [record for record in reviews if id(record) not in actionable_ids]
    decision_counts = Counter(record.workflow_decision for record in reviews)
    decision_summary = "".join(
        f'<span class="summary-chip"><strong>{decision_counts[decision]}</strong> {escape(decision)}</span>'
        for decision in DECISION_ORDER
        if decision_counts[decision]
    )
    title = f"{generated_on} - Daily Job Workflow"
    storage_key = json.dumps(f"jobagent-human-search:{generated_on}").replace("<", r"\u003c")
    resume_ready_storage_key = json.dumps(f"jobagent-resume-ready:{generated_on}").replace("<", r"\u003c")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
:root {{ color-scheme: light; --ink:#172033; --muted:#667085; --line:#dce2ea; --panel:#fff; --canvas:#f4f7fb; --navy:#173b63; --green:#137a4b; --amber:#a55b08; --red:#b42318; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--canvas); color:var(--ink); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
main {{ width:min(1500px, calc(100% - 32px)); margin:0 auto 64px; }}
.hero {{ margin:24px 0; padding:28px; border-radius:18px; color:#fff; background:linear-gradient(120deg,#122e4b,#1b5d73); box-shadow:0 12px 35px rgba(23,59,99,.18); }}
.hero h1 {{ margin:0 0 8px; font-size:clamp(26px,4vw,42px); line-height:1.1; }}
.hero p {{ margin:0; color:#dbeaf3; }}
.hero .companion-link {{ display:inline-block; margin-top:14px; padding:8px 12px; border:1px solid #b9d8e8; border-radius:8px; color:#fff; font-weight:700; text-decoration:none; }}
.hero .companion-link:hover {{ background:rgba(255,255,255,.1); }}
.metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:20px 0; }}
.metric {{ padding:18px; background:var(--panel); border:1px solid var(--line); border-radius:14px; box-shadow:0 3px 12px rgba(23,32,51,.05); }}
.metric strong {{ display:block; font-size:28px; color:var(--navy); }}
.decision-summary {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 26px; }}
.summary-chip,.decision,.count {{ display:inline-flex; align-items:center; gap:5px; padding:5px 9px; border-radius:999px; background:#e9eef5; font-size:12px; font-weight:700; letter-spacing:.02em; }}
section.panel,.decision-group {{ margin:24px 0; padding:22px; background:var(--panel); border:1px solid var(--line); border-radius:16px; box-shadow:0 4px 16px rgba(23,32,51,.05); }}
h2 {{ margin:0 0 16px; color:var(--navy); }}
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; min-width:980px; }}
th,td {{ padding:12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
th {{ background:#edf3f8; color:#344054; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
.review-card {{ padding:18px; margin:14px 0; border:1px solid var(--line); border-radius:13px; background:#fbfcfe; }}
.review-card-heading {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }}
.review-card h3 {{ margin:0 0 4px; font-size:19px; }}
.meta,.muted {{ color:var(--muted); }}
.meta {{ margin:0; }}
.action {{ padding:10px 12px; background:#edf3f8; border-radius:9px; }}
.detail-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
.detail-list {{ padding:11px 13px; border-left:4px solid #98a2b3; background:#fff; border-radius:8px; }}
.detail-list.positive {{ border-left-color:var(--green); }} .detail-list.warning {{ border-left-color:var(--amber); }} .detail-list.danger {{ border-left-color:var(--red); }}
.detail-list h4 {{ margin:0 0 6px; }} .detail-list ul {{ margin:0; padding-left:18px; }}
.card-footer {{ display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:14px; color:var(--muted); }}
.resolution-controls {{ display:flex; align-items:center; gap:10px; margin-top:14px; }}
.batch-resolution {{ display:flex; justify-content:space-between; align-items:center; gap:20px; margin:0 0 18px; padding:16px; border:1px solid #9bc6e2; border-radius:12px; background:#eef7fc; }}
.batch-resolution h3,.batch-resolution p {{ margin:0; }} .batch-resolution p {{ margin-top:4px; color:var(--muted); }}
.copy-resolution {{ appearance:none; border:1px solid #0b63a5; border-radius:8px; padding:9px 12px; color:#fff; background:#0b63a5; font:inherit; font-weight:700; cursor:pointer; }}
.copy-resolution:hover {{ background:#084f83; }} .copy-resolution:focus-visible {{ outline:3px solid #9ed2f5; outline-offset:2px; }}
.copy-status {{ min-height:1.5em; color:var(--muted); font-size:13px; }}
.job-link,.resume-link {{ color:#0b63a5; font-weight:700; text-decoration:none; }} .job-link:hover,.resume-link:hover {{ text-decoration:underline; }}
.resume-missing {{ color:var(--muted); font-weight:600; white-space:nowrap; }}
.resume-ready-check-label {{ display:inline-flex; align-items:center; gap:7px; font-weight:700; white-space:nowrap; cursor:pointer; }}
.resume-ready-check {{ width:18px; height:18px; accent-color:var(--green); cursor:pointer; }}
tr:has(.resume-ready-check:checked) {{ background:#e7f6ee; }}
.resume-ready-status {{ margin:-8px 0 16px; color:var(--muted); }}
.clickable-job-row {{ cursor:pointer; transition:background-color .15s ease; }}
.clickable-job-row:hover,.clickable-job-row:focus {{ background:#edf6fb; outline:none; }}
.clickable-job-row:focus-visible {{ box-shadow:inset 0 0 0 3px #75b7df; }}
.detail-link {{ color:#0b63a5; font-weight:700; text-decoration:none; }}
.detail-link:hover {{ text-decoration:underline; }}
.decision.strong-apply,.decision.apply {{ color:#08633d; background:#d9f2e5; }}
.decision.moderate-stretch {{ color:#8a4b05; background:#fff0d5; }}
.decision.extreme-stretch,.decision.skip {{ color:#9f2017; background:#fee4e2; }}
.empty-state {{ padding:24px; color:var(--muted); border:1px dashed #98a2b3; border-radius:10px; text-align:center; }}
.report-tabs {{ display:flex; gap:8px; margin:20px 0; padding-bottom:10px; border-bottom:1px solid var(--line); }}
.tab-button,.reset-checklist {{ border:1px solid var(--line); border-radius:8px; padding:9px 14px; background:#fff; color:var(--ink); font:inherit; font-weight:700; cursor:pointer; }}
.tab-button[aria-selected="true"] {{ border-color:var(--navy); background:var(--navy); color:#fff; }}
.tab-panel[hidden] {{ display:none; }}
.diagnostic-archive {{ margin:24px 0; padding:18px 22px; border:1px solid var(--line); border-radius:14px; background:var(--panel); }}
.diagnostic-archive > summary {{ cursor:pointer; color:var(--navy); font-size:18px; font-weight:800; }}
.company-checklist {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; margin:0; padding:0; list-style:none; }}
.company-checklist li {{ display:flex; gap:12px; padding:16px; border:1px solid var(--line); border-radius:10px; background:#fbfcfe; }}
.company-checklist li:has(.company-check:checked) {{ border-color:var(--green); background:#e7f6ee; }}
.company-check {{ width:20px; height:20px; margin-top:3px; accent-color:var(--green); }}
.company-name {{ display:block; font-weight:800; }} .company-url {{ display:block; margin-top:5px; color:#0b63a5; }}
.checklist-header {{ display:flex; justify-content:space-between; align-items:center; gap:16px; }}
.storage-status {{ min-height:1.5em; color:var(--muted); }}
@media (max-width:800px) {{ .metrics,.detail-grid {{ grid-template-columns:1fr; }} .review-card-heading,.card-footer {{ flex-direction:column; }} main {{ width:min(100% - 20px,1500px); }} }}
</style>
</head>
<body>
<main>
<header class="hero"><h1>{escape(title)}</h1><p>This is the actionable queue. Only jobs in Resume Ready may proceed to resume or cover-letter generation.</p><a class="companion-link" href="{escape(f'{generated_on}-daily-job-search.html')}">Open broad discovery artifact</a></header>
<nav class="report-tabs" role="tablist" aria-label="Daily job workflow views">
  <button class="tab-button" id="actions-tab" type="button" role="tab" aria-selected="true" aria-controls="actions-panel" data-tab-target="actions-panel">Action Queue</button>
  <button class="tab-button" id="companies-tab" type="button" role="tab" aria-selected="false" aria-controls="companies-panel" data-tab-target="companies-panel">Companies to Check</button>
</nav>
<section class="tab-panel" id="actions-panel" role="tabpanel" aria-labelledby="actions-tab">
<div class="metrics">
  <div class="metric"><strong>{len(ready)}</strong>Resume ready</div>
  <div class="metric"><strong>{len(gap_questions)}</strong>Gap questions</div>
  <div class="metric"><strong>{len(applied)}</strong>Already applied</div>
  <div class="metric"><strong>{len(diagnostics)}</strong>Diagnostics</div>
</div>
<section class="panel" id="resume-ready"><h2>Resume Ready</h2><p class="resume-ready-status" id="resume-ready-status" aria-live="polite">Select a role to open its full review, documents, and interview guidance.</p>{render_ready_table(ready, generated_on=generated_on)}</section>
<section class="panel" id="gap-questions"><h2>Gap Questions for Candidate</h2><p>Answer these before any resume work. Copying a resolution prompt does not authorize a resume until the reviewer is rerun.</p>{render_batch_resolution_controls(gap_questions, generated_on)}{"".join(render_review_card(record) for record in gap_questions) if gap_questions else '<div class="empty-state">No jobs need gap answers.</div>'}</section>
<section class="panel" id="already-applied"><h2>Already Applied</h2><p>These tracker matches are excluded from resume production.</p>{render_applied_table(applied)}</section>
<details class="diagnostic-archive"><summary>Discovery diagnostics and non-actionable reviews ({len(diagnostics)})</summary><p>Moderate Stretch, Extreme Stretch, Skip, and raw discovery results are retained here for auditing; they are not resume authorization.</p><div class="decision-summary">{decision_summary}</div>{render_review_groups(diagnostics)}<p><a href="{escape(f'{generated_on}-daily-job-search.html')}">Open the raw discovery artifact</a> or <a href="{escape(f'{generated_on}-job-inspection-queue.md')}">open the inspection queue</a>.</p></details>
</section>
<section class="tab-panel" id="companies-panel" role="tabpanel" aria-labelledby="companies-tab" hidden>
  <section class="panel">{render_company_checklist(human_search_companies)}</section>
</section>
</main>
<script>
const tabButtons = Array.from(document.querySelectorAll(".tab-button"));
const tabPanels = Array.from(document.querySelectorAll(".tab-panel"));
for (const button of tabButtons) {{
  button.addEventListener("click", () => {{
    for (const candidate of tabButtons) candidate.setAttribute("aria-selected", String(candidate === button));
    for (const panel of tabPanels) panel.hidden = panel.id !== button.dataset.tabTarget;
  }});
}}
const checklistStorageKey = {storage_key};
const resumeReadyStorageKey = {resume_ready_storage_key};
const companyCheckboxes = Array.from(document.querySelectorAll(".company-check"));
const resumeReadyCheckboxes = Array.from(document.querySelectorAll(".resume-ready-check"));
const clickableJobRows = Array.from(document.querySelectorAll(".clickable-job-row"));
const resumeReadyStatus = document.getElementById("resume-ready-status");
const checklistProgress = document.getElementById("checklist-progress");
const storageStatus = document.querySelector(".storage-status");
const resetChecklist = document.querySelector(".reset-checklist");
if (companyCheckboxes.length > 0) {{
  if (!(checklistProgress instanceof HTMLElement) || !(storageStatus instanceof HTMLElement) || !(resetChecklist instanceof HTMLButtonElement)) throw new Error("Company checklist controls are incomplete.");
  const updateChecklistProgress = () => {{ checklistProgress.textContent = `${{companyCheckboxes.filter((box) => box.checked).length}} of ${{companyCheckboxes.length}} searched`; }};
  const saveChecklist = () => {{
    localStorage.setItem(checklistStorageKey, JSON.stringify(Object.fromEntries(companyCheckboxes.map((box) => [box.dataset.company, box.checked]))));
    storageStatus.textContent = "Checklist progress saved in this browser.";
  }};
  try {{
    const stored = localStorage.getItem(checklistStorageKey);
    if (stored !== null) {{
      const parsed = JSON.parse(stored);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("Stored checklist state is invalid.");
      for (const box of companyCheckboxes) box.checked = parsed[box.dataset.company] === true;
    }}
  }} catch (error) {{ storageStatus.textContent = "Saved checklist progress was unavailable; starting unchecked."; }}
  updateChecklistProgress();
  for (const box of companyCheckboxes) box.addEventListener("change", () => {{ updateChecklistProgress(); saveChecklist(); }});
  resetChecklist.addEventListener("click", () => {{ for (const box of companyCheckboxes) box.checked = false; updateChecklistProgress(); saveChecklist(); }});
}}
if (resumeReadyCheckboxes.length > 0) {{
  if (!(resumeReadyStatus instanceof HTMLElement)) throw new Error("Resume-ready application controls are incomplete.");
  const saveResumeReadyProgress = () => {{
    try {{
      localStorage.setItem(resumeReadyStorageKey, JSON.stringify(Object.fromEntries(resumeReadyCheckboxes.map((box) => [box.dataset.jobUrl, box.checked]))));
      resumeReadyStatus.textContent = "Resume-ready application progress saved in this browser.";
    }} catch (error) {{
      resumeReadyStatus.textContent = "Application checks could not be saved in this browser.";
    }}
  }};
  try {{
    const stored = localStorage.getItem(resumeReadyStorageKey);
    if (stored !== null) {{
      const parsed = JSON.parse(stored);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("Stored resume-ready state is invalid.");
      for (const box of resumeReadyCheckboxes) box.checked = parsed[box.dataset.jobUrl] === true;
    }}
  }} catch (error) {{
    resumeReadyStatus.textContent = "Saved application checks were unavailable; starting unchecked.";
  }}
  for (const box of resumeReadyCheckboxes) box.addEventListener("change", saveResumeReadyProgress);
}}
const navigateToJobDetail = (row) => {{
  const href = row.dataset.detailHref;
  if (!href) throw new Error("Clickable job row is missing its detail URL.");
  window.location.href = href;
}};
for (const row of clickableJobRows) {{
  row.addEventListener("click", (event) => {{
    const target = event.target;
    if (target instanceof Element && target.closest("a,button,input,label")) return;
    navigateToJobDetail(row);
  }});
  row.addEventListener("keydown", (event) => {{
    if (event.key === "Enter" || event.key === " ") {{
      event.preventDefault();
      navigateToJobDetail(row);
    }}
  }});
}}
async function copyResolutionPrompt(button) {{
  const prompt = button.dataset.resolutionPrompt;
  const status = button.parentElement.querySelector(".copy-status");
  if (!prompt || !status) {{
    throw new Error("Resolution prompt controls are incomplete.");
  }}
  try {{
    if (navigator.clipboard && window.isSecureContext) {{
      await navigator.clipboard.writeText(prompt);
    }} else {{
      const textarea = document.createElement("textarea");
      textarea.value = prompt;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      textarea.remove();
      if (!copied) throw new Error("Browser copy command failed.");
    }}
    status.textContent = "Resolution prompt copied.";
  }} catch (error) {{
    status.textContent = "Copy failed — select and copy the questions manually.";
  }}
}}
document.addEventListener("click", (event) => {{
  const target = event.target;
  if (target instanceof HTMLButtonElement && target.classList.contains("copy-resolution")) {{
    void copyResolutionPrompt(target);
  }}
}});
</script>
</body>
</html>
"""


def default_output_path(review_path: Path) -> Path:
    if review_path.name.endswith(".json"):
        return review_path.with_suffix(".html")
    return review_path.with_name(f"{review_path.name}.html")


def write_workflow_detail_pages(
    *,
    generated_on: str,
    reviews: list[ReviewRecord],
    destination: Path,
    resume_dir: Path,
    produced_artifacts: Mapping[tuple[str, str], Path],
    produced_cover_letters: Mapping[tuple[str, str], Path],
    networking_sections: Mapping[str, NetworkingCompanySection],
) -> list[Path]:
    ready = sorted(
        (record for record in reviews if record.should_generate_resume and not record.already_applied),
        key=lambda record: (-record.source_fit_score, record.company, record.title),
    )
    detail_dir = destination.parent / f"{generated_on}-workflow-review-details"
    detail_dir.mkdir(parents=True, exist_ok=True)
    expected_paths = {detail_dir / detail_page_filename(record) for record in ready}
    for stale_path in detail_dir.glob("*.html"):
        if stale_path not in expected_paths:
            stale_path.unlink()
    written_paths: list[Path] = []
    for record in ready:
        detail_path = detail_dir / detail_page_filename(record)
        detail_path.write_text(
            render_workflow_detail_document(
                generated_on=generated_on,
                record=record,
                resume_dir=resume_dir,
                detail_dir=detail_dir,
                produced_artifacts=produced_artifacts,
                produced_cover_letters=produced_cover_letters,
                networking=networking_sections.get(normalize_company_key(record.company)),
            ),
            encoding="utf-8",
        )
        written_paths.append(detail_path)
    return written_paths


def write_workflow_review_html(*, review_path: Path, output_path: Path | None = None) -> Path:
    generated_on, reviews = load_workflow_review(review_path)
    destination = output_path or default_output_path(review_path)
    human_search_companies = load_human_search_companies(
        review_path.parent / f"{generated_on}-human-search-companies.md"
    )
    produced_artifacts: dict[tuple[str, str], Path] = {}
    manifest_path = review_path.parent / f"{generated_on}-resume-production.json"
    if manifest_path.exists():
        for artifact in load_validated_resume_artifacts(manifest_path):
            key = (artifact.company.casefold(), artifact.url)
            if key in produced_artifacts:
                raise ValueError(f"Duplicate job URL in resume production manifest: {artifact.url}")
            produced_artifacts[key] = artifact.artifact_path
    cover_letter_manifest_path = review_path.parent / f"{generated_on}-cover-letter-production.json"
    produced_cover_letters = (
        load_validated_cover_letter_artifacts(cover_letter_manifest_path)
        if cover_letter_manifest_path.exists()
        else {}
    )
    networking_sections = load_networking_sections(
        review_path.parent / f"{generated_on}-networking-matches.md"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    resume_dir = destination.parent / "resumes"
    write_workflow_detail_pages(
        generated_on=generated_on,
        reviews=reviews,
        destination=destination,
        resume_dir=resume_dir,
        produced_artifacts=produced_artifacts,
        produced_cover_letters=produced_cover_letters,
        networking_sections=networking_sections,
    )
    destination.write_text(
        render_document(
            generated_on=generated_on,
            reviews=reviews,
            resume_dir=resume_dir,
            produced_artifacts=produced_artifacts,
            produced_cover_letters=produced_cover_letters,
            human_search_companies=human_search_companies,
        ),
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Render strict workflow review JSON as HTML.")
    parser.add_argument("--workflow-review", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    output_path = write_workflow_review_html(review_path=args.workflow_review, output_path=args.out)
    print(f"Wrote workflow review HTML: {output_path}")


if __name__ == "__main__":
    main()
