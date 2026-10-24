#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from lxml import etree as LET

if __package__:
    from scripts.docx_compatibility import (
        WORD_SETTINGS_PATH,
        modernize_word_settings_xml,
        require_modern_word_compatibility,
    )
    from scripts.mark_applied_jobs import (
        ReportJob,
        canonicalize_url,
        extract_requisition_id,
        load_tracker_jobs_snapshot,
        match_tracker_job,
    )
    from scripts.render_workflow_review_html import write_workflow_review_html
    from scripts.resume_artifacts import (
        ResumeReadyJob,
        ValidatedResumeArtifact,
        load_validated_resume_artifacts,
        parse_resume_ready_jobs,
        resume_artifact_name,
        resume_artifact_names,
        run_date_from_ready_path,
    )
else:
    from docx_compatibility import (
        WORD_SETTINGS_PATH,
        modernize_word_settings_xml,
        require_modern_word_compatibility,
    )
    from mark_applied_jobs import (
        ReportJob,
        canonicalize_url,
        extract_requisition_id,
        load_tracker_jobs_snapshot,
        match_tracker_job,
    )
    from render_workflow_review_html import write_workflow_review_html
    from resume_artifacts import (
        ResumeReadyJob,
        ValidatedResumeArtifact,
        load_validated_resume_artifacts,
        parse_resume_ready_jobs,
        resume_artifact_name,
        resume_artifact_names,
        run_date_from_ready_path,
    )


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS = {"w": W_NS}
DOCUMENT_XML = "word/document.xml"
DOCUMENT_RELS_XML = "word/_rels/document.xml.rels"
HYPERLINK_REL_TYPE = f"{R_NS}/hyperlink"
CONTACT_LINKS: tuple[tuple[str, str], ...] = (
    ("Email", "mailto:candidate@example.com"),
    ("LinkedIn", "https://www.linkedin.com/in/candidate-profile"),
    ("AI Portfolio", "https://portfolio.example.com"),
)
REQUIRED_DOCX_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    DOCUMENT_XML,
    DOCUMENT_RELS_XML,
    "word/styles.xml",
    WORD_SETTINGS_PATH,
}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RenderFunction = Callable[[Path, Path], list[Path]]
DEFAULT_DOCKER_RENDERER_IMAGE = "jobsearchagent-libreoffice-renderer:1.0.0"
DOCKER_RENDERER_IMAGE_ENV = "JOBAGENT_DOCKER_RENDERER_IMAGE"
RENDERER_MODE_ENV = "JOBAGENT_RENDERER_MODE"
CANDIDATE_NAME_ENV = "JOBAGENT_CANDIDATE_NAME"
NATIVE_RENDER_TIMEOUT_SECONDS = 30
DOCKER_RENDER_TIMEOUT_SECONDS = 90
PDF_RASTER_TIMEOUT_SECONDS = 30
OPENING_SUMMARY_SPACE_BEFORE_TWIPS = 240

if TYPE_CHECKING:
    from scripts.produce_cover_letters import CoverLetterProductionResult


def configured_contact_links() -> tuple[tuple[str, str], ...]:
    email = os.environ.get("JOBAGENT_CANDIDATE_EMAIL", "candidate@example.com").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("JOBAGENT_CANDIDATE_EMAIL must be a valid nonempty email address")
    links = (
        ("Email", f"mailto:{email}"),
        ("LinkedIn", os.environ.get(
            "JOBAGENT_RESUME_LINKEDIN_URL",
            os.environ.get("JOBAGENT_LINKEDIN_URL", CONTACT_LINKS[1][1]),
        ).strip()),
        ("AI Portfolio", os.environ.get(
            "JOBAGENT_RESUME_PORTFOLIO_URL",
            os.environ.get("JOBAGENT_PORTFOLIO_URL", CONTACT_LINKS[2][1]),
        ).strip()),
    )
    for label, target in links[1:]:
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{label} contact link must be an absolute HTTP or HTTPS URL")
    return links


@dataclass(frozen=True)
class SourceTemplate:
    filename: str
    sha256: str
    source_title: str
    summary_prefix: str


@dataclass(frozen=True)
class ResumeProductionRecord:
    company: str
    title: str
    url: str
    template: str
    overqualified_risk: str
    resume_positioning: str
    artifact_path: str
    status: str
    validation_status: str
    page_count: int
    source_sha256: str
    artifact_sha256: str


@dataclass(frozen=True)
class ResumeProductionResult:
    generated_on: str
    records: list[ResumeProductionRecord]
    manifest_path: Path
    workflow_html_path: Path


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def string_field(item: Mapping[str, Any], key: str, template_name: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Template {template_name!r} requires a non-empty {key!r} string")
    return value.strip()


def load_template_specs(path: Path) -> dict[str, SourceTemplate]:
    if not path.exists():
        raise FileNotFoundError(f"Missing resume template configuration: {path}")
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a top-level object in {path}")
    specs: dict[str, SourceTemplate] = {}
    for template_name, raw_value in payload.items():
        if not isinstance(template_name, str) or not isinstance(raw_value, dict):
            raise ValueError(f"Invalid template configuration entry in {path}")
        specs[template_name] = SourceTemplate(
            filename=string_field(raw_value, "filename", template_name),
            sha256=string_field(raw_value, "sha256", template_name),
            source_title=string_field(raw_value, "source_title", template_name),
            summary_prefix=string_field(raw_value, "summary_prefix", template_name),
        )
    return specs


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))


def replace_paragraph_text(paragraph: ET.Element, expected: str, replacement: str) -> None:
    actual = paragraph_text(paragraph)
    if actual != expected:
        raise ValueError(f"Template slot mismatch: expected {expected!r}, found {actual!r}")
    text_nodes = paragraph.findall(".//w:t", NS)
    if not text_nodes:
        raise ValueError(f"Editable template slot has no text nodes: {expected!r}")
    text_nodes[0].text = replacement
    for node in text_nodes[1:]:
        node.text = ""


def positioned_summary(*, job: ResumeReadyJob, spec: SourceTemplate, source_summary: str) -> str:
    summary = f"{job.title}{source_summary.removeprefix(spec.summary_prefix)}"
    if job.resume_positioning != "senior_lead_execution_forward":
        return summary
    summary = re.sub(
        r"\b(?:over|more than)\s+\d+\s+years(?:\s+of\s+experience)?\b",
        "extensive experience",
        summary,
        flags=re.IGNORECASE,
    )
    summary = re.sub(
        r"\bprincipal-level leadership\b",
        "technical leadership",
        summary,
        flags=re.IGNORECASE,
    )
    summary = re.sub(
        r"\benterprise-wide architecture\b",
        "enterprise application design",
        summary,
        flags=re.IGNORECASE,
    )
    return summary


def ensure_opening_summary_spacing(paragraph: LET._Element) -> None:
    paragraph_properties = paragraph.find("w:pPr", NS)
    if paragraph_properties is None:
        paragraph_properties = LET.Element(f"{{{W_NS}}}pPr")
        paragraph.insert(0, paragraph_properties)
    spacing = paragraph_properties.find("w:spacing", NS)
    if spacing is None:
        spacing = LET.SubElement(paragraph_properties, f"{{{W_NS}}}spacing")
    spacing.set(f"{{{W_NS}}}before", str(OPENING_SUMMARY_SPACE_BEFORE_TWIPS))


def next_relationship_id(relationships_root: LET._Element) -> str:
    numeric_ids = [
        int(match.group(1))
        for relationship in relationships_root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        if (match := re.fullmatch(r"rId(\d+)", relationship.get("Id", ""))) is not None
    ]
    return f"rId{max(numeric_ids, default=0) + 1}"


def add_hyperlink_relationship(relationships_root: LET._Element, target: str) -> str:
    relationship_id = next_relationship_id(relationships_root)
    relationship = LET.SubElement(
        relationships_root,
        f"{{{PACKAGE_REL_NS}}}Relationship",
    )
    relationship.set("Id", relationship_id)
    relationship.set("Type", HYPERLINK_REL_TYPE)
    relationship.set("Target", target)
    relationship.set("TargetMode", "External")
    return relationship_id


def styled_run(text: str, reference_run: LET._Element | None, *, hyperlink: bool) -> LET._Element:
    run = LET.Element(f"{{{W_NS}}}r")
    reference_properties = reference_run.find("w:rPr", NS) if reference_run is not None else None
    if reference_properties is not None:
        run.append(copy.deepcopy(reference_properties))
    if hyperlink:
        properties = run.find("w:rPr", NS)
        if properties is None:
            properties = LET.Element(f"{{{W_NS}}}rPr")
            run.insert(0, properties)
        color = properties.find("w:color", NS)
        if color is None:
            color = LET.SubElement(properties, f"{{{W_NS}}}color")
        color.set(f"{{{W_NS}}}val", "000000")
        underline = properties.find("w:u", NS)
        if underline is None:
            underline = LET.SubElement(properties, f"{{{W_NS}}}u")
        underline.set(f"{{{W_NS}}}val", "single")
    text_node = LET.SubElement(run, f"{{{W_NS}}}t")
    if text.startswith(" ") or text.endswith(" "):
        text_node.set(f"{{{XML_NS}}}space", "preserve")
    text_node.text = text
    return run


def rewrite_contact_header(
    document_root: LET._Element,
    relationships_root: LET._Element,
) -> None:
    body = document_root.find("w:body", NS)
    if body is None:
        raise ValueError("Source template is missing word/document.xml w:body")
    title_table = body.find("w:tbl", NS)
    if title_table is None:
        raise ValueError("Source template is missing the retained title table")
    title_cells = title_table.findall("./w:tr/w:tc", NS)
    if len(title_cells) < 2:
        raise ValueError("Source template title table is missing the contact cell")
    contact_paragraph = title_cells[1].find("w:p", NS)
    if contact_paragraph is None:
        raise ValueError("Source template contact cell is missing its first paragraph")

    hyperlink_reference = contact_paragraph.find(".//w:hyperlink/w:r", NS)
    plain_reference = contact_paragraph.find("w:r", NS)
    if hyperlink_reference is None:
        hyperlink_reference = plain_reference
    for child in list(contact_paragraph):
        if child.tag != f"{{{W_NS}}}pPr":
            contact_paragraph.remove(child)

    for index, (label, target) in enumerate(configured_contact_links()):
        if index:
            contact_paragraph.append(styled_run(" • ", plain_reference, hyperlink=False))
        hyperlink = LET.SubElement(contact_paragraph, f"{{{W_NS}}}hyperlink")
        hyperlink.set(f"{{{R_NS}}}id", add_hyperlink_relationship(relationships_root, target))
        hyperlink.append(styled_run(label, hyperlink_reference, hyperlink=True))


def tailor_document_parts(
    document_xml: bytes,
    relationships_xml: bytes,
    *,
    job: ResumeReadyJob,
    spec: SourceTemplate,
) -> tuple[bytes, bytes]:
    root = LET.fromstring(
        document_xml,
        parser=LET.XMLParser(remove_blank_text=False, resolve_entities=False),
    )
    relationships_root = LET.fromstring(
        relationships_xml,
        parser=LET.XMLParser(remove_blank_text=False, resolve_entities=False),
    )
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("Source template is missing word/document.xml w:body")
    tables = body.findall("w:tbl", NS)
    if not tables:
        raise ValueError("Source template is missing the retained title table")
    title_paragraphs = tables[0].findall("./w:tr/w:tc/w:p", NS)
    if len(title_paragraphs) < 2:
        raise ValueError("Source template title cell does not contain a target-role paragraph")
    replace_paragraph_text(title_paragraphs[1], spec.source_title, job.title)

    summary_paragraph = next(
        (paragraph for paragraph in body.findall("w:p", NS) if paragraph_text(paragraph).startswith(spec.summary_prefix)),
        None,
    )
    if summary_paragraph is None:
        raise ValueError(f"Source template is missing summary prefix: {spec.summary_prefix!r}")
    source_summary = paragraph_text(summary_paragraph)
    tailored_summary = positioned_summary(job=job, spec=spec, source_summary=source_summary)
    replace_paragraph_text(summary_paragraph, source_summary, tailored_summary)
    ensure_opening_summary_spacing(summary_paragraph)
    rewrite_contact_header(root, relationships_root)
    return (
        LET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        LET.tostring(relationships_root, encoding="UTF-8", xml_declaration=True, standalone=True),
    )


def build_resume_from_template(
    *, source_path: Path, destination_path: Path, job: ResumeReadyJob, spec: SourceTemplate
) -> None:
    with zipfile.ZipFile(source_path, "r") as source:
        document_xml = source.read(DOCUMENT_XML)
        relationships_xml = source.read(DOCUMENT_RELS_XML)
        tailored_xml, tailored_relationships = tailor_document_parts(
            document_xml,
            relationships_xml,
            job=job,
            spec=spec,
        )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination_path, "w") as destination:
            for entry in source.infolist():
                if entry.filename == DOCUMENT_XML:
                    content = tailored_xml
                elif entry.filename == DOCUMENT_RELS_XML:
                    content = tailored_relationships
                elif entry.filename == WORD_SETTINGS_PATH:
                    content = modernize_word_settings_xml(source.read(entry.filename))
                else:
                    content = source.read(entry.filename)
                destination.writestr(entry, content)


def validate_docx(*, source_path: Path, resume_path: Path, expected_title: str) -> None:
    if not resume_path.is_file() or resume_path.stat().st_size == 0:
        raise ValueError(f"Resume artifact is missing or empty: {resume_path}")
    if not zipfile.is_zipfile(resume_path):
        raise ValueError(f"Resume artifact is not a valid DOCX package: {resume_path}")
    with zipfile.ZipFile(source_path, "r") as source, zipfile.ZipFile(resume_path, "r") as resume:
        source_names = source.namelist()
        resume_names = resume.namelist()
        missing_parts = REQUIRED_DOCX_PARTS.difference(resume_names)
        if missing_parts:
            raise ValueError(f"Resume artifact is missing DOCX parts: {sorted(missing_parts)}")
        if source_names != resume_names:
            raise ValueError(f"Resume artifact package members differ from source template: {resume_path}")
        changed_parts = [name for name in source_names if source.read(name) != resume.read(name)]
        allowed_changed_parts = {DOCUMENT_XML, DOCUMENT_RELS_XML, WORD_SETTINGS_PATH}
        if any(name not in allowed_changed_parts for name in changed_parts):
            raise ValueError(f"Resume artifact changed preserve-only package parts: {changed_parts}")
        require_modern_word_compatibility(
            resume.read(WORD_SETTINGS_PATH),
            document_path=resume_path,
        )
        document_xml = resume.read(DOCUMENT_XML)
        document_root = ET.fromstring(document_xml)
        compatibility_root = LET.fromstring(document_xml)
        ignorable_prefixes = str(
            compatibility_root.get(f"{{{MC_NS}}}Ignorable", "")
        ).split()
        declared_prefixes = {prefix for prefix in compatibility_root.nsmap if prefix is not None}
        undeclared_prefixes = sorted(set(ignorable_prefixes).difference(declared_prefixes))
        if undeclared_prefixes:
            raise ValueError(
                "Resume artifact has undeclared Word compatibility namespace prefix(es): "
                f"{undeclared_prefixes}"
            )
        text = " ".join(node.text or "" for node in document_root.findall(".//w:t", NS))
        if expected_title not in text:
            raise ValueError(f"Resume artifact does not contain target title {expected_title!r}: {resume_path}")
        contact_paragraph = next(
            (
                paragraph
                for paragraph in compatibility_root.findall(".//w:p", NS)
                if paragraph_text(paragraph) == "Email • LinkedIn • AI Portfolio"
            ),
            None,
        )
        if contact_paragraph is None:
            raise ValueError(f"Resume artifact has an obsolete contact header: {resume_path}")
        relationships_root = LET.fromstring(resume.read(DOCUMENT_RELS_XML))
        relationship_targets = {
            relationship.get("Id", ""): relationship.get("Target", "")
            for relationship in relationships_root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        }
        contact_targets = {
            paragraph_text(hyperlink): relationship_targets.get(
                hyperlink.get(f"{{{R_NS}}}id", ""),
                "",
            )
            for hyperlink in contact_paragraph.findall("w:hyperlink", NS)
        }
        if contact_targets != dict(configured_contact_links()):
            raise ValueError(
                f"Resume artifact contact hyperlinks are invalid: {contact_targets!r} in {resume_path}"
            )


def resolve_binary(name: str, macos_fallback: Path | None = None) -> str:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    if macos_fallback is not None and macos_fallback.is_file():
        return str(macos_fallback)
    raise FileNotFoundError(f"Required rendering binary is unavailable: {name}")


def render_docx_native(document_path: Path, render_dir: Path) -> list[Path]:
    soffice = resolve_binary("soffice", Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    pdftoppm = resolve_binary("pdftoppm", Path("/opt/homebrew/bin/pdftoppm"))
    render_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = render_dir / ".libreoffice-profile"
    profile_dir.mkdir()
    environment = os.environ.copy()
    environment["HOME"] = str(profile_dir)
    environment["TMPDIR"] = "/private/tmp"
    command = [
        soffice,
        "--headless",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(render_dir),
        str(document_path),
    ]
    try:
        conversion = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=environment,
            timeout=NATIVE_RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"LibreOffice rendering timed out after {NATIVE_RENDER_TIMEOUT_SECONDS} seconds "
            f"for {document_path}"
        ) from exc
    if conversion.returncode != 0:
        raise RuntimeError(f"LibreOffice rendering failed for {document_path}: {conversion.stderr.strip()}")
    pdf_path = render_dir / f"{document_path.stem}.pdf"
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"LibreOffice did not create a PDF for {document_path}")
    raster_command = [
        pdftoppm,
        "-png",
        "-r",
        "144",
        str(pdf_path),
        str(render_dir / "page"),
    ]
    try:
        raster = subprocess.run(
            raster_command,
            capture_output=True,
            text=True,
            env=environment,
            timeout=PDF_RASTER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"PDF rasterization timed out after {PDF_RASTER_TIMEOUT_SECONDS} seconds "
            f"for {document_path}"
        ) from exc
    if raster.returncode != 0:
        raise RuntimeError(f"PDF rasterization failed for {document_path}: {raster.stderr.strip()}")
    return sorted(render_dir.glob("page-*.png"))


def docker_renderer_image() -> str:
    image = os.environ.get(DOCKER_RENDERER_IMAGE_ENV, DEFAULT_DOCKER_RENDERER_IMAGE).strip()
    if not image:
        raise ValueError(f"{DOCKER_RENDERER_IMAGE_ENV} cannot be empty")
    return image


def renderer_mode() -> str:
    mode = os.environ.get(RENDERER_MODE_ENV, "auto").strip().casefold()
    if mode not in {"auto", "docker"}:
        raise ValueError(f"{RENDERER_MODE_ENV} must be 'auto' or 'docker', got {mode!r}")
    return mode


def render_docx_in_docker(document_path: Path, render_dir: Path) -> list[Path]:
    docker = resolve_binary("docker")
    resolved_document_path = document_path.resolve()
    resolved_render_dir = render_dir.resolve()
    resolved_render_dir.mkdir(parents=True, exist_ok=True)
    command = [
        docker,
        "run",
        "--rm",
        "--network",
        "none",
        "--mount",
        f"type=bind,src={resolved_document_path.parent},dst=/input,readonly",
        "--mount",
        f"type=bind,src={resolved_render_dir},dst=/output",
        docker_renderer_image(),
        resolved_document_path.name,
    ]
    try:
        conversion = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=DOCKER_RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Docker LibreOffice rendering timed out after {DOCKER_RENDER_TIMEOUT_SECONDS} "
            f"seconds for {document_path}"
        ) from exc
    if conversion.returncode != 0:
        detail = conversion.stderr.strip() or conversion.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"Docker LibreOffice rendering failed for {document_path}: {detail}")
    page_paths = sorted(render_dir.glob("page-*.png"))
    if not page_paths:
        raise RuntimeError(f"Docker LibreOffice did not create rendered pages for {document_path}")
    return page_paths


def render_docx(document_path: Path, render_dir: Path) -> list[Path]:
    if renderer_mode() == "docker":
        return render_docx_in_docker(document_path, render_dir)
    try:
        return render_docx_native(document_path, render_dir)
    except (FileNotFoundError, RuntimeError) as native_error:
        try:
            return render_docx_in_docker(document_path, render_dir)
        except (FileNotFoundError, RuntimeError) as docker_error:
            raise RuntimeError(
                f"Resume rendering failed for {document_path}; native renderer failed: {native_error}; "
                f"container renderer failed: {docker_error}"
            ) from docker_error


def validate_rendered_pages(page_paths: list[Path], *, document_path: Path) -> None:
    if not page_paths:
        raise ValueError(f"Resume rendering produced no pages: {document_path}")
    for page_path in page_paths:
        content = page_path.read_bytes()
        if len(content) < 24 or not content.startswith(PNG_SIGNATURE):
            raise ValueError(f"Resume rendering produced an invalid PNG: {page_path}")
        width, height = struct.unpack(">II", content[16:24])
        if width <= 0 or height <= 0:
            raise ValueError(f"Resume rendering produced invalid page dimensions: {page_path}")


def workflow_review_path(resume_ready_path: Path, run_date: str) -> Path:
    return resume_ready_path.parent / f"{run_date}-workflow-review.json"


def reject_applied_jobs(
    *, jobs: list[ResumeReadyJob], applied_jobs_path: Path, run_date: str
) -> None:
    tracker_jobs = load_tracker_jobs_snapshot(applied_jobs_path, expected_date=run_date)
    for job in jobs:
        tracker_match = match_tracker_job(
            ReportJob(
                company=job.company,
                title=job.title,
                posted="",
                location="",
                fit=str(job.fit),
                url=job.url,
            ),
            tracker_jobs,
        )
        if tracker_match is not None and tracker_match.already_applied:
            evidence = tracker_match.applied_date or tracker_match.status or "jobtracker match"
            raise ValueError(
                f"Resume-ready queue contains already applied job: {job.company} — {job.title} ({evidence})"
            )


def preflight_sources(
    jobs: list[ResumeReadyJob], source_dir: Path, specs: Mapping[str, SourceTemplate]
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for job in jobs:
        if job.template not in specs:
            raise ValueError(f"No source resume mapping exists for template: {job.template}")
        spec = specs[job.template]
        source_path = source_dir / spec.filename
        if not source_path.exists():
            raise FileNotFoundError(f"Missing source resume for {job.template}: {source_path}")
        actual_hash = sha256_path(source_path)
        if actual_hash != spec.sha256:
            raise ValueError(
                f"Source resume hash changed for {job.template}: expected {spec.sha256}, found {actual_hash}"
            )
        paths[job.template] = source_path
    return paths


def write_manifest(path: Path, *, run_date: str, records: list[ResumeProductionRecord]) -> None:
    payload = {"generated_on": run_date, "records": [asdict(record) for record in records]}
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def same_job_posting(job: ResumeReadyJob, artifact: ValidatedResumeArtifact) -> bool:
    if job.company.casefold() != artifact.company.casefold():
        return False
    job_requisition = extract_requisition_id(job.url)
    artifact_requisition = extract_requisition_id(artifact.url)
    if job_requisition and artifact_requisition:
        return job_requisition.casefold() == artifact_requisition.casefold()
    return canonicalize_url(job.url) == canonicalize_url(artifact.url)


def reusable_resume_artifacts(
    *, output_root: Path, jobs: list[ResumeReadyJob]
) -> dict[ResumeReadyJob, Path]:
    reusable: dict[ResumeReadyJob, Path] = {}
    manifest_paths = sorted(output_root.glob("????-??-??-resume-production.json"), reverse=True)
    for manifest_path in manifest_paths:
        for artifact in load_validated_resume_artifacts(manifest_path):
            if not artifact.artifact_path.is_file() or artifact.artifact_path.stat().st_size == 0:
                continue
            for job in jobs:
                if job not in reusable and same_job_posting(job, artifact):
                    reusable[job] = artifact.artifact_path
    return reusable


def produce_resumes(
    *,
    resume_ready_path: Path,
    applied_jobs_path: Path,
    source_dir: Path,
    output_dir: Path,
    render_fn: RenderFunction | None = None,
    template_specs: Mapping[str, SourceTemplate] | None = None,
    template_config_path: Path | None = None,
    candidate_name: str = "Candidate Name",
) -> ResumeProductionResult:
    run_date = run_date_from_ready_path(resume_ready_path)
    jobs = parse_resume_ready_jobs(resume_ready_path)
    artifact_names = resume_artifact_names(run_date, jobs, candidate_name=candidate_name)
    reject_applied_jobs(jobs=jobs, applied_jobs_path=applied_jobs_path, run_date=run_date)
    if template_specs is None:
        config_path = template_config_path or Path(__file__).resolve().parents[1] / "resume_templates.json"
        specs: Mapping[str, SourceTemplate] = load_template_specs(config_path)
    else:
        specs = template_specs
    source_paths = preflight_sources(jobs, source_dir, specs)
    review_path = workflow_review_path(resume_ready_path, run_date)
    if not review_path.exists():
        raise FileNotFoundError(f"Missing workflow review needed to regenerate HTML: {review_path}")

    actual_render_fn = render_fn or render_docx
    reusable_paths = reusable_resume_artifacts(output_root=resume_ready_path.parent, jobs=jobs)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staged_paths: dict[Path, Path] = {}
    records: list[ResumeProductionRecord] = []
    with tempfile.TemporaryDirectory(prefix="resume-production-", dir=output_dir.parent) as temporary_dir:
        staging_root = Path(temporary_dir)
        for job in jobs:
            spec = specs[job.template]
            source_path = source_paths[job.template]
            artifact_name = artifact_names[job]
            final_path = output_dir / artifact_name
            if final_path.exists():
                candidate_path = final_path
                status = "existing"
                try:
                    validate_docx(
                        source_path=source_path,
                        resume_path=candidate_path,
                        expected_title=job.title,
                    )
                except ValueError:
                    candidate_path = staging_root / "documents" / artifact_name
                    build_resume_from_template(
                        source_path=source_path,
                        destination_path=candidate_path,
                        job=job,
                        spec=spec,
                    )
                    staged_paths[final_path] = candidate_path
                    status = "generated"
            elif job in reusable_paths:
                candidate_path = reusable_paths[job]
                final_path = candidate_path
                status = "reused"
                try:
                    validate_docx(
                        source_path=source_path,
                        resume_path=candidate_path,
                        expected_title=job.title,
                    )
                except ValueError:
                    candidate_path = staging_root / "documents" / artifact_name
                    build_resume_from_template(
                        source_path=source_path,
                        destination_path=candidate_path,
                        job=job,
                        spec=spec,
                    )
                    staged_paths[final_path] = candidate_path
                    status = "generated"
            else:
                candidate_path = staging_root / "documents" / artifact_name
                build_resume_from_template(
                    source_path=source_path,
                    destination_path=candidate_path,
                    job=job,
                    spec=spec,
                )
                staged_paths[final_path] = candidate_path
                status = "generated"

            validate_docx(source_path=source_path, resume_path=candidate_path, expected_title=job.title)
            page_paths = actual_render_fn(candidate_path, staging_root / "renders" / Path(artifact_name).stem)
            validate_rendered_pages(page_paths, document_path=candidate_path)
            records.append(
                ResumeProductionRecord(
                    company=job.company,
                    title=job.title,
                    url=job.url,
                    template=job.template,
                    overqualified_risk=job.overqualified_risk,
                    resume_positioning=job.resume_positioning,
                    artifact_path=str(final_path),
                    status=status,
                    validation_status="validated",
                    page_count=len(page_paths),
                    source_sha256=spec.sha256,
                    artifact_sha256=sha256_path(candidate_path),
                )
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        for final_path, staged_path in staged_paths.items():
            os.replace(staged_path, final_path)

    manifest_path = resume_ready_path.parent / f"{run_date}-resume-production.json"
    write_manifest(manifest_path, run_date=run_date, records=records)
    workflow_html_path = write_workflow_review_html(review_path=review_path)
    return ResumeProductionResult(
        generated_on=run_date,
        records=records,
        manifest_path=manifest_path,
        workflow_html_path=workflow_html_path,
    )


def produce_cover_letters_for_ready_queue(
    *,
    resume_ready_path: Path,
    applied_jobs_path: Path,
    source_dir: Path,
    resume_output_dir: Path,
) -> "CoverLetterProductionResult":
    if __package__:
        from scripts.produce_cover_letters import produce_cover_letters
    else:
        from produce_cover_letters import produce_cover_letters

    authorization_run_date = run_date_from_ready_path(resume_ready_path)
    return produce_cover_letters(
        resume_ready_path=resume_ready_path,
        workflow_review_path=workflow_review_path(resume_ready_path, authorization_run_date),
        applied_jobs_path=applied_jobs_path,
        source_dir=source_dir,
        output_dir=resume_output_dir.parent / "cover_letters",
        generated_on=authorization_run_date,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and validate missing resumes authorized by a resume-ready jobs file."
    )
    parser.add_argument("--resume-ready", required=True, type=Path)
    parser.add_argument("--applied-jobs", required=True, type=Path)
    parser.add_argument("--source-dir", type=Path, default=Path("Source Docs"))
    parser.add_argument("--out", type=Path, default=Path("outputs/resumes"))
    parser.add_argument("--template-config", type=Path, default=Path("resume_templates.json"))
    args = parser.parse_args()
    result = produce_resumes(
        resume_ready_path=args.resume_ready,
        applied_jobs_path=args.applied_jobs,
        source_dir=args.source_dir,
        output_dir=args.out,
        template_config_path=args.template_config,
        candidate_name=os.environ.get(CANDIDATE_NAME_ENV, "Candidate Name"),
    )
    cover_letter_result = produce_cover_letters_for_ready_queue(
        resume_ready_path=args.resume_ready,
        applied_jobs_path=args.applied_jobs,
        source_dir=args.source_dir,
        resume_output_dir=args.out,
    )
    write_workflow_review_html(
        review_path=workflow_review_path(args.resume_ready, run_date_from_ready_path(args.resume_ready))
    )
    generated_count = sum(record.status == "generated" for record in result.records)
    existing_count = sum(record.status == "existing" for record in result.records)
    reused_count = sum(record.status == "reused" for record in result.records)
    print(
        f"Validated {len(result.records)} resume(s): {generated_count} generated, "
        f"{existing_count} current-day existing, {reused_count} prior artifact(s) reused"
    )
    print(f"Wrote resume production manifest: {result.manifest_path}")
    print(f"Regenerated workflow review HTML: {result.workflow_html_path}")
    generated_cover_letters = sum(
        record.status == "generated" for record in cover_letter_result.records
    )
    existing_cover_letters = sum(
        record.status == "existing" for record in cover_letter_result.records
    )
    print(
        f"Validated {len(cover_letter_result.records)} cover letter(s): "
        f"{generated_cover_letters} generated, {existing_cover_letters} existing"
    )
    print(f"Wrote cover-letter production manifest: {cover_letter_result.manifest_path}")


if __name__ == "__main__":
    main()
