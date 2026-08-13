#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode


NETWORKING_START = "<!-- networking-pass:start -->"
NETWORKING_END = "<!-- networking-pass:end -->"
DECISION_SECTION_MAP = {
    "Apply": "Apply",
    "Maybe": "Maybe",
    "Discovery Candidates — Higher Signal": "Apply",
    "Discovery Candidates — Review Signal": "Maybe",
}
DECISION_SECTIONS = tuple(DECISION_SECTION_MAP)


@dataclass(frozen=True)
class ReportRow:
    decision: str
    priority: str
    company: str
    role: str
    posted: str
    location_work_model: str
    fit: str
    url: str
    already_applied: str


@dataclass(frozen=True)
class Contact:
    priority: str
    name: str
    current_company: str
    current_role: str
    linkedin_url: str
    connected_on: str
    match_source: str
    why_reach_out: str


@dataclass(frozen=True)
class NetworkingCompanySection:
    company: str
    relevant_jobs: list[str]
    contacts: list[Contact]
    no_contacts_message: str | None


@dataclass(frozen=True)
class IntroductionMessage:
    company: str
    contact_name: str
    body: str


@dataclass(frozen=True)
class LinkedinPeopleSearch:
    label: str
    query: str
    url: str


@dataclass(frozen=True)
class HumanSearchCompany:
    name: str
    careers_url: str


def remove_networking_appendix(markdown: str) -> str:
    pattern = re.compile(
        rf"\n?{re.escape(NETWORKING_START)}(?P<body>.*?){re.escape(NETWORKING_END)}",
        re.DOTALL,
    )

    def replacement(match: re.Match[str]) -> str:
        notes = extract_recruiter_add_notes(match.group("body"))
        return f"\n\n{notes}" if notes else ""

    return pattern.sub(replacement, markdown).strip()


def extract_recruiter_add_notes(markdown: str) -> str:
    lines = markdown.splitlines()
    try:
        start = lines.index("## LinkedIn Recruiter Add Notes")
    except ValueError:
        return ""

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def human_search_section_bounds(lines: list[str]) -> tuple[int, int] | None:
    try:
        start = lines.index("## Human Search Calibration")
    except ValueError:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## ") or lines[index] == NETWORKING_START:
            end = index
            break
    return start, end


def extract_human_search_companies(markdown: str) -> list[HumanSearchCompany]:
    lines = markdown.splitlines()
    bounds = human_search_section_bounds(lines)
    if bounds is None:
        return []
    start, end = bounds
    companies: list[HumanSearchCompany] = []
    for line in lines[start + 1 : end]:
        match = re.fullmatch(r"\s*\d+\.\s+(.+?)\s+-\s+(https?://\S+)\s*", line)
        if match is not None:
            companies.append(HumanSearchCompany(name=match.group(1).strip(), careers_url=match.group(2)))
    if not companies:
        raise ValueError("Human Search Calibration section contains no numbered company URLs")
    return companies


def remove_human_search_section(markdown: str) -> str:
    lines = markdown.splitlines()
    bounds = human_search_section_bounds(lines)
    if bounds is None:
        return markdown
    start, end = bounds
    return "\n".join([*lines[:start], *lines[end:]]).strip()


def convert_inline_markdown(text: str) -> str:
    escaped = html.escape(text, quote=True)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        escaped,
    )
    escaped = re.sub(
        r"(?<![\">])(https?://[^\s<]+)",
        r'<a href="\1">\1</a>',
        escaped,
    )
    return escaped


def split_table_row(line: str) -> list[str]:
    raw_cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [cell.replace(r"\|", "|").strip() for cell in raw_cells]


def escape_table_cell(value: str) -> str:
    return value.replace("|", r"\|")


def is_separator_row(line: str) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in split_table_row(line))


def render_table(lines: list[str], start_index: int) -> tuple[str, int]:
    table_lines: list[str] = []
    index = start_index
    while index < len(lines) and lines[index].strip().startswith("|"):
        table_lines.append(lines[index].strip())
        index += 1

    if len(table_lines) < 2:
        raise ValueError("Markdown table is missing a separator row.")

    header_cells = split_table_row(table_lines[0])
    body_lines = [line for line in table_lines[2:] if not is_separator_row(line)]
    already_applied_index = next(
        (
            index
            for index, cell in enumerate(header_cells)
            if cell.strip().casefold() == "already applied"
        ),
        None,
    )

    output: list[str] = ['<div class="table-wrap">', "<table>", "<thead>", "<tr>"]
    for cell in header_cells:
        output.append(f"<th>{convert_inline_markdown(cell)}</th>")
    output.extend(["</tr>", "</thead>", "<tbody>"])

    for line in body_lines:
        row_cells = split_table_row(line)
        row_class = ""
        if (
            already_applied_index is not None
            and len(row_cells) > already_applied_index
            and row_cells[already_applied_index].strip().casefold() == "no"
        ):
            row_class = ' class="needs-application"'
        output.append(f"<tr{row_class}>")
        for cell in row_cells:
            output.append(f"<td>{convert_inline_markdown(cell)}</td>")
        output.append("</tr>")

    output.extend(["</tbody>", "</table>", "</div>"])
    return "\n".join(output), index


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    blocks: list[str] = []
    index = 0

    while index < len(lines):
        stripped = lines[index].strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < len(lines) and lines[index + 1].strip().startswith("|"):
            table_html, index = render_table(lines, index)
            blocks.append(table_html)
            continue

        heading_match = re.fullmatch(r"(#{1,6})\s+(.*)", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            blocks.append(f"<h{level}>{convert_inline_markdown(heading_match.group(2))}</h{level}>")
            index += 1
            continue

        if stripped.startswith("- "):
            items: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("- "):
                items.append(lines[index].strip()[2:])
                index += 1
            blocks.append(
                "<ul>\n" + "\n".join(f"<li>{convert_inline_markdown(item)}</li>" for item in items) + "\n</ul>"
            )
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            next_stripped = lines[index].strip()
            if not next_stripped:
                index += 1
                break
            if (
                next_stripped.startswith("|")
                or next_stripped.startswith("- ")
                or re.fullmatch(r"(#{1,6})\s+(.*)", next_stripped)
            ):
                break
            paragraph_lines.append(next_stripped)
            index += 1

        blocks.append(f"<p>{convert_inline_markdown(' '.join(paragraph_lines))}</p>")

    return "\n".join(blocks)


def extract_title(*, report_markdown: str, fallback_stem: str) -> str:
    for line in report_markdown.splitlines():
        heading_match = re.fullmatch(r"#\s+(.*)", line.strip())
        if heading_match:
            return heading_match.group(1).strip()
    return fallback_stem.removesuffix("-daily-job-search")


def slugify(value: str) -> str:
    lowered = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered)
    return normalized.strip("-")


def extract_table_rows(lines: list[str], start_index: int) -> tuple[list[list[str]], int]:
    table_lines: list[str] = []
    index = start_index
    while index < len(lines) and lines[index].strip().startswith("|"):
        table_lines.append(lines[index].strip())
        index += 1

    if len(table_lines) < 2:
        raise ValueError("Expected a markdown table with a separator row.")

    rows: list[list[str]] = []
    for line in table_lines[2:]:
        if is_separator_row(line):
            continue
        rows.append(split_table_row(line))
    return rows, index


def parse_report_rows(report_markdown: str) -> list[ReportRow]:
    lines = remove_networking_appendix(report_markdown).splitlines()
    rows: list[ReportRow] = []
    index = 0
    current_section: str | None = None

    while index < len(lines):
        stripped = lines[index].strip()
        heading_match = re.fullmatch(r"##\s+(.*)", stripped)
        if heading_match:
            current_section = heading_match.group(1).strip()
            index += 1
            continue

        if current_section in DECISION_SECTIONS and stripped.startswith("|"):
            raw_rows, index = extract_table_rows(lines, index)
            for raw_row in raw_rows:
                if len(raw_row) != 8:
                    raise ValueError(f"Expected 8 columns in {current_section} table, found {len(raw_row)}.")
                rows.append(
                    ReportRow(
                        decision=DECISION_SECTION_MAP[current_section],
                        priority=raw_row[0],
                        company=raw_row[1],
                        role=raw_row[2],
                        posted=raw_row[3],
                        location_work_model=raw_row[4],
                        fit=raw_row[5],
                        url=raw_row[6],
                        already_applied=raw_row[7],
                    )
                )
            continue

        index += 1

    return rows


def parse_networking_markdown(networking_markdown: str) -> tuple[dict[str, NetworkingCompanySection], dict[tuple[str, str], IntroductionMessage]]:
    lines = networking_markdown.splitlines()
    company_sections: dict[str, NetworkingCompanySection] = {}
    messages: dict[tuple[str, str], IntroductionMessage] = {}
    index = 0
    current_major_section = ""

    while index < len(lines):
        stripped = lines[index].strip()

        major_match = re.fullmatch(r"##\s+(.*)", stripped)
        if major_match:
            current_major_section = major_match.group(1).strip()
            index += 1
            continue

        if current_major_section == "People to Contact":
            company_match = re.fullmatch(r"###\s+(.*)", stripped)
            if company_match:
                company, next_index, section = parse_company_networking_section(lines, index)
                company_sections[company] = section
                index = next_index
                continue

        if current_major_section == "Introduction Messages":
            message_match = re.fullmatch(r"###\s+(.*)\s+-\s+(.*)", stripped)
            if message_match:
                company = message_match.group(1).strip()
                contact_name = message_match.group(2).strip()
                body_lines: list[str] = []
                index += 1
                while index < len(lines):
                    next_stripped = lines[index].strip()
                    if re.fullmatch(r"###\s+.*", next_stripped):
                        break
                    if re.fullmatch(r"##\s+.*", next_stripped):
                        break
                    body_lines.append(lines[index].rstrip())
                    index += 1

                body = "\n".join(line for line in body_lines).strip()
                messages[(company, contact_name)] = IntroductionMessage(
                    company=company,
                    contact_name=contact_name,
                    body=body,
                )
                continue

        index += 1

    return company_sections, messages


def parse_company_networking_section(
    lines: list[str],
    start_index: int,
) -> tuple[str, int, NetworkingCompanySection]:
    company_match = re.fullmatch(r"###\s+(.*)", lines[start_index].strip())
    if not company_match:
        raise ValueError("Expected a company heading in networking markdown.")

    company = company_match.group(1).strip()
    index = start_index + 1
    relevant_jobs: list[str] = []
    contacts: list[Contact] = []
    no_contacts_message: str | None = None

    while index < len(lines):
        stripped = lines[index].strip()

        if re.fullmatch(r"###\s+.*", stripped) or re.fullmatch(r"##\s+.*", stripped):
            break

        if stripped == "Relevant jobs:":
            index += 1
            while index < len(lines) and lines[index].strip().startswith("- "):
                relevant_jobs.append(lines[index].strip()[2:])
                index += 1
            continue

        if stripped.startswith("|"):
            raw_rows, index = extract_table_rows(lines, index)
            for raw_row in raw_rows:
                if len(raw_row) != 8:
                    raise ValueError(f"Expected 8 columns in contact table for {company}, found {len(raw_row)}.")
                contacts.append(
                    Contact(
                        priority=raw_row[0],
                        name=raw_row[1],
                        current_company=raw_row[2],
                        current_role=raw_row[3],
                        linkedin_url=extract_markdown_link_url(raw_row[4]),
                        connected_on=raw_row[5],
                        match_source=raw_row[6],
                        why_reach_out=raw_row[7],
                    )
                )
            continue

        if stripped.startswith("No current-company LinkedIn contacts found."):
            no_contacts_message = stripped

        index += 1

    return (
        company,
        index,
        NetworkingCompanySection(
            company=company,
            relevant_jobs=relevant_jobs,
            contacts=contacts,
            no_contacts_message=no_contacts_message,
        ),
    )


def extract_markdown_link_url(value: str) -> str:
    link_match = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", value.strip())
    if not link_match:
        raise ValueError(f"Expected markdown link cell, found: {value}")
    return link_match.group(2)


def build_default_paths(report_path: Path) -> tuple[Path, Path]:
    if not report_path.name.endswith("-daily-job-search.md"):
        raise ValueError("Report filename must end with '-daily-job-search.md'.")

    date_prefix = report_path.name.removesuffix("-daily-job-search.md")
    networking_path = report_path.with_name(f"{date_prefix}-networking-matches.md")
    output_path = report_path.with_name(f"{date_prefix}-daily-job-search.html")
    return networking_path, output_path


def build_detail_directory(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}-contacts")


def build_detail_filename(row: ReportRow) -> str:
    prefix = slugify(row.decision)
    priority = slugify(row.priority)
    company = slugify(row.company)
    role = slugify(row.role)
    return f"{prefix}-{priority}-{company}-{role}.html"


def build_linkedin_people_searches(row: ReportRow) -> list[LinkedinPeopleSearch]:
    search_specs = [
        (
            "Recruiters / Talent Acquisition",
            f'"{row.company}" ("recruiter" OR "talent acquisition" OR "technical recruiter")',
        ),
        (
            "Engineering Hiring Managers",
            f'"{row.company}" ("engineering manager" OR "software engineering manager" OR "director engineering")',
        ),
        (
            "Role-Specific Technical Leads",
            f'"{row.company}" "{row.role}" ("hiring" OR "manager" OR "lead" OR "principal")',
        ),
        (
            "Professional Services / Solutions Leaders",
            f'"{row.company}" ("professional services" OR "solutions architect" OR "technical consultant" OR "delivery architect")',
        ),
    ]
    return [
        LinkedinPeopleSearch(
            label=label,
            query=query,
            url="https://www.linkedin.com/search/results/people/?" + urlencode({"keywords": query}, quote_via=quote),
        )
        for label, query in search_specs
    ]


def linkedin_connection_note(row: ReportRow) -> str:
    return (
        f"Hi, I am looking at the {row.role} role at {row.company}. "
        "My background is senior Java/backend, enterprise integrations, SQL, production support, "
        "and technical delivery. Would you be open to pointing me toward the right hiring contact?"
    )


def insert_detail_links(report_markdown: str, detail_links: dict[tuple[str, str, str], str]) -> str:
    lines = remove_networking_appendix(report_markdown).splitlines()
    output: list[str] = []
    index = 0
    current_section: str | None = None

    while index < len(lines):
        stripped = lines[index].strip()
        heading_match = re.fullmatch(r"##\s+(.*)", stripped)
        if heading_match:
            current_section = heading_match.group(1).strip()
            output.append(lines[index])
            index += 1
            continue

        if current_section in DECISION_SECTIONS and stripped.startswith("|"):
            raw_rows, index = extract_table_rows(lines, index)
            output.append("| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |")
            output.append("|---|---|---|---|---|---|---|---|")
            for raw_row in raw_rows:
                canonical_decision = DECISION_SECTION_MAP[current_section]
                key = (canonical_decision, raw_row[0], raw_row[1], raw_row[2])
                if key not in detail_links:
                    raise ValueError(f"Missing detail link for row: {key}")
                row_copy = list(raw_row)
                row_copy[2] = f"{row_copy[2]} ([Contacts]({detail_links[key]}))"
                output.append("| " + " | ".join(escape_table_cell(cell) for cell in row_copy) + " |")
            continue

        output.append(lines[index])
        index += 1

    return "\n".join(output)


def render_document(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title, quote=True)}</title>
  <style>
    :root {{
      --text: #24292f;
      --muted: #57606a;
      --line: #d0d7de;
      --surface: #ffffff;
      --code-bg: #f6f8fa;
      --accent: #0969da;
      --table-head: #f6f8fa;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      background: var(--surface);
      font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    main {{
      width: 100%;
      max-width: none;
      margin: 0;
      padding: 28px 32px 48px;
    }}
    h1, h2, h3 {{
      line-height: 1.25;
      margin: 24px 0 16px;
    }}
    h1 {{
      padding-bottom: 0.3em;
      border-bottom: 1px solid var(--line);
      font-size: 2em;
      margin-top: 0;
    }}
    h2 {{
      padding-bottom: 0.3em;
      border-bottom: 1px solid var(--line);
      font-size: 1.5em;
    }}
    h3 {{
      font-size: 1.25em;
    }}
    p {{
      margin: 0 0 16px;
    }}
    ul {{
      margin: 0 0 16px 24px;
      padding: 0;
    }}
    li {{
      margin: 0.25em 0;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    code {{
      background: var(--code-bg);
      border-radius: 6px;
      font: 85% ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      padding: 0.2em 0.4em;
    }}
    .table-wrap {{
      width: 100%;
      overflow-x: auto;
      margin: 0 0 16px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      display: table;
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 6px 13px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: var(--table-head);
      font-weight: 600;
    }}
    tr:nth-child(2n) {{
      background: #fbfbfb;
    }}
    tr.needs-application {{
      background: #eaf4ff;
    }}
    tr.needs-application:nth-child(2n) {{
      background: #eaf4ff;
    }}
    .meta {{
      color: var(--muted);
      margin-top: -8px;
      margin-bottom: 24px;
    }}
    .back-link {{
      display: inline-block;
      margin-bottom: 20px;
    }}
    .note-box {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px 14px;
      margin: 16px 0;
      background: #f6f8fa;
    }}
    .message-block {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 16px;
      margin: 16px 0;
      background: #fff;
      white-space: pre-wrap;
    }}
    .search-list {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 10px;
      list-style: none;
      margin-left: 0;
    }}
    .search-list li {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      background: #f6f8fa;
    }}
    .report-tabs {{
      display: flex;
      gap: 8px;
      margin: 0 0 24px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
    }}
    .tab-button {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px 16px;
      color: var(--text);
      background: var(--code-bg);
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }}
    .tab-button[aria-selected="true"] {{
      border-color: var(--accent);
      color: #ffffff;
      background: var(--accent);
    }}
    .companion-report-link {{
      margin-left: auto;
      padding: 9px 12px;
      border: 1px solid var(--accent);
      border-radius: 8px;
      font-weight: 650;
      white-space: nowrap;
    }}
    .tab-button:focus-visible, .company-check:focus-visible, .reset-checklist:focus-visible {{
      outline: 3px solid #9ecbff;
      outline-offset: 2px;
    }}
    .tab-panel[hidden] {{ display: none; }}
    .checklist-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 18px;
    }}
    .checklist-header h1 {{ margin-bottom: 4px; }}
    .checklist-summary {{ color: var(--muted); }}
    .company-checklist {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 14px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .company-checklist li {{
      display: flex;
      align-items: flex-start;
      gap: 12px;
      min-height: 86px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--code-bg);
    }}
    .company-checklist li:has(.company-check:checked) {{
      border-color: #2da44e;
      background: #dafbe1;
    }}
    .company-check {{
      width: 20px;
      height: 20px;
      margin: 3px 0 0;
      accent-color: #2da44e;
      cursor: pointer;
    }}
    .company-name {{ display: block; font-weight: 700; }}
    .company-url {{ display: block; margin-top: 4px; overflow-wrap: anywhere; }}
    .reset-checklist {{
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 12px;
      background: #ffffff;
      font: inherit;
      cursor: pointer;
    }}
    .storage-status {{ min-height: 1.5em; margin-top: 14px; color: var(--muted); }}
    @media (max-width: 700px) {{
      main {{ padding: 20px 16px 36px; }}
      .checklist-header {{ align-items: flex-start; flex-direction: column; }}
      .report-tabs {{ position: sticky; top: 0; z-index: 2; padding-top: 10px; background: var(--surface); }}
      .companion-report-link {{ margin-left: 0; }}
    }}
  </style>
</head>
<body>
  <main>
    {body_html}
  </main>
</body>
</html>
"""


def render_company_checklist(companies: list[HumanSearchCompany]) -> str:
    items: list[str] = []
    for index, company in enumerate(companies, start=1):
        checkbox_id = f"company-check-{index}"
        items.append(
            "<li>"
            f'<input class="company-check" type="checkbox" id="{checkbox_id}" '
            f'data-company="{html.escape(company.name, quote=True)}">'
            "<div>"
            f'<label class="company-name" for="{checkbox_id}">{html.escape(company.name)}</label>'
            f'<a class="company-url" href="{html.escape(company.careers_url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">Open company careers</a>'
            "</div>"
            "</li>"
        )
    return (
        '<div class="checklist-header"><div>'
        "<h1>Companies to Check</h1>"
        '<p class="checklist-summary"><strong id="checklist-progress">'
        f"0 of {len(companies)} searched</strong></p>"
        "</div>"
        '<button class="reset-checklist" type="button">Reset checklist</button></div>'
        '<p>Manually search these career sites and compare any jobs found against today\'s report.</p>'
        f'<ul class="company-checklist">{"".join(items)}</ul>'
        '<p class="storage-status" role="status" aria-live="polite"></p>'
    )


def render_report_tabs(*, title: str, jobs_html: str, companies: list[HumanSearchCompany]) -> str:
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", title)
    if date_match is None:
        raise ValueError("Daily report title must begin with YYYY-MM-DD")
    workflow_review_href = f"{date_match.group(1)}-workflow-review.html"
    storage_key = json.dumps(f"jobagent-human-search:{title}").replace("<", r"\u003c")
    return f"""
<nav class="report-tabs" role="tablist" aria-label="Daily job search views">
  <button class="tab-button" id="jobs-tab" type="button" role="tab" aria-selected="true" aria-controls="jobs-panel" data-tab-target="jobs-panel">Jobs</button>
  <button class="tab-button" id="companies-tab" type="button" role="tab" aria-selected="false" aria-controls="companies-panel" data-tab-target="companies-panel">Companies to Check</button>
  <a class="companion-report-link" href="{html.escape(workflow_review_href, quote=True)}">Open Strict Workflow Review</a>
</nav>
<section class="tab-panel" id="jobs-panel" role="tabpanel" aria-labelledby="jobs-tab">
  {jobs_html}
</section>
<section class="tab-panel" id="companies-panel" role="tabpanel" aria-labelledby="companies-tab" hidden>
  {render_company_checklist(companies)}
</section>
<script>
(() => {{
  const tabButtons = Array.from(document.querySelectorAll(".tab-button"));
  const tabPanels = Array.from(document.querySelectorAll(".tab-panel"));
  for (const button of tabButtons) {{
    button.addEventListener("click", () => {{
      const targetId = button.dataset.tabTarget;
      for (const candidate of tabButtons) {{
        candidate.setAttribute("aria-selected", String(candidate === button));
      }}
      for (const panel of tabPanels) {{
        panel.hidden = panel.id !== targetId;
      }}
    }});
  }}

  const storageKey = {storage_key};
  const checkboxes = Array.from(document.querySelectorAll(".company-check"));
  const progress = document.getElementById("checklist-progress");
  const storageStatus = document.querySelector(".storage-status");
  const resetButton = document.querySelector(".reset-checklist");
  if (!(progress instanceof HTMLElement) || !(storageStatus instanceof HTMLElement) ||
      !(resetButton instanceof HTMLButtonElement)) {{
    throw new Error("Company checklist controls are incomplete.");
  }}

  const updateProgress = () => {{
    const completed = checkboxes.filter((checkbox) => checkbox.checked).length;
    progress.textContent = `${{completed}} of ${{checkboxes.length}} searched`;
  }};
  const stateFromCheckboxes = () => Object.fromEntries(
    checkboxes.map((checkbox) => [checkbox.dataset.company, checkbox.checked])
  );
  const saveState = () => {{
    try {{
      localStorage.setItem(storageKey, JSON.stringify(stateFromCheckboxes()));
      storageStatus.textContent = "Checklist progress saved in this browser.";
    }} catch (error) {{
      storageStatus.textContent = "Checklist progress could not be saved; it will last only while this page stays open.";
    }}
  }};

  try {{
    const stored = localStorage.getItem(storageKey);
    if (stored !== null) {{
      const parsed = JSON.parse(stored);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {{
        throw new Error("Stored checklist state is invalid.");
      }}
      for (const checkbox of checkboxes) {{
        checkbox.checked = parsed[checkbox.dataset.company] === true;
      }}
    }}
  }} catch (error) {{
    storageStatus.textContent = "Saved checklist progress was unavailable; starting with an unchecked list.";
  }}
  updateProgress();

  for (const checkbox of checkboxes) {{
    checkbox.addEventListener("change", () => {{
      updateProgress();
      saveState();
    }});
  }}
  resetButton.addEventListener("click", () => {{
    for (const checkbox of checkboxes) checkbox.checked = false;
    updateProgress();
    saveState();
  }});
}})();
</script>
"""


def render_landing_page(title: str, report_markdown: str, detail_links: dict[tuple[str, str, str], str]) -> str:
    enhanced_markdown = insert_detail_links(report_markdown, detail_links)
    companies = extract_human_search_companies(enhanced_markdown)
    jobs_markdown = remove_human_search_section(enhanced_markdown)
    jobs_html = markdown_to_html(jobs_markdown)
    return render_document(
        title,
        render_report_tabs(title=title, jobs_html=jobs_html, companies=companies),
    )


def render_contact_detail_page(
    *,
    row: ReportRow,
    title: str,
    company_section: NetworkingCompanySection | None,
    company_messages: list[IntroductionMessage],
    backlink: str,
) -> str:
    parts: list[str] = [
        f'<a class="back-link" href="{html.escape(backlink, quote=True)}">&larr; Back to report</a>',
        f"<h1>{html.escape(row.role)}</h1>",
        f'<p class="meta">{html.escape(title)} | {html.escape(row.decision)} | {html.escape(row.company)}</p>',
        "<h2>Role Summary</h2>",
        '<div class="table-wrap"><table><tbody>',
        f"<tr><th>Company</th><td>{html.escape(row.company)}</td></tr>",
        f"<tr><th>Decision</th><td>{html.escape(row.decision)}</td></tr>",
        f"<tr><th>Priority</th><td>{html.escape(row.priority)}</td></tr>",
        f"<tr><th>Posted</th><td>{html.escape(row.posted)}</td></tr>",
        f"<tr><th>Location / Work Model</th><td>{html.escape(row.location_work_model)}</td></tr>",
        f"<tr><th>Fit</th><td>{html.escape(row.fit)}</td></tr>",
        f"<tr><th>Already Applied</th><td>{html.escape(row.already_applied)}</td></tr>",
        f'<tr><th>Job URL</th><td><a href="{html.escape(row.url, quote=True)}">{html.escape(row.url)}</a></td></tr>',
        "</tbody></table></div>",
        "<h2>Who to Contact</h2>",
    ]

    if company_section is None:
        parts.append('<div class="note-box">No networking section was found for this company.</div>')
    elif company_section.contacts:
        parts.append(render_contacts_table(company_section.contacts))
    elif company_section.no_contacts_message is not None:
        parts.append(f'<div class="note-box">{convert_inline_markdown(company_section.no_contacts_message)}</div>')
    else:
        parts.append('<div class="note-box">No contact data was available for this company.</div>')

    parts.append("<h2>Suggested Contact Messages</h2>")
    if company_messages:
        for message in company_messages:
            parts.append(f"<h3>{html.escape(message.contact_name)}</h3>")
            parts.append(f'<div class="message-block">{html.escape(message.body)}</div>')
    else:
        parts.append('<div class="note-box">No contact message was generated for this company.</div>')

    parts.append("<h2>LinkedIn Searches</h2>")
    parts.append(
        "<ul class=\"search-list\">\n"
        + "\n".join(
            f'<li><strong>{html.escape(search.label)}</strong><br>'
            f'<code>{html.escape(search.query)}</code><br>'
            f'<a href="{html.escape(search.url, quote=True)}">Open LinkedIn people search</a></li>'
            for search in build_linkedin_people_searches(row)
        )
        + "\n</ul>"
    )
    parts.append("<h2>Add Note for New LinkedIn Connections</h2>")
    parts.append(f'<div class="message-block">{html.escape(linkedin_connection_note(row))}</div>')

    if company_section is not None and company_section.relevant_jobs:
        parts.append("<h2>Other Relevant Jobs From This Company</h2>")
        parts.append(
            "<ul>\n" + "\n".join(f"<li>{convert_inline_markdown(item)}</li>" for item in company_section.relevant_jobs) + "\n</ul>"
        )

    return render_document(f"{row.role} - Contacts", "\n".join(parts))


def render_contacts_table(contacts: list[Contact]) -> str:
    rows: list[str] = [
        '<div class="table-wrap"><table>',
        "<thead><tr><th>Priority</th><th>Name</th><th>Current Company</th><th>Current Role</th><th>LinkedIn</th><th>Connected On</th><th>Match Source</th><th>Why Reach Out</th></tr></thead>",
        "<tbody>",
    ]
    for contact in contacts:
        rows.append("<tr>")
        rows.append(f"<td>{html.escape(contact.priority)}</td>")
        rows.append(f"<td>{html.escape(contact.name)}</td>")
        rows.append(f"<td>{html.escape(contact.current_company)}</td>")
        rows.append(f"<td>{html.escape(contact.current_role)}</td>")
        rows.append(
            f'<td><a href="{html.escape(contact.linkedin_url, quote=True)}">{html.escape(contact.linkedin_url)}</a></td>'
        )
        rows.append(f"<td>{html.escape(contact.connected_on)}</td>")
        rows.append(f"<td>{html.escape(contact.match_source)}</td>")
        rows.append(f"<td>{html.escape(contact.why_reach_out)}</td>")
        rows.append("</tr>")
    rows.extend(["</tbody>", "</table></div>"])
    return "\n".join(rows)


def write_html_report(
    *,
    report_path: Path,
    networking_path: Path | None = None,
    output_path: Path | None = None,
) -> Path:
    default_networking_path, default_output_path = build_default_paths(report_path)
    resolved_networking_path = networking_path or default_networking_path
    resolved_output_path = output_path or default_output_path

    report_markdown = report_path.read_text(encoding="utf-8")
    networking_markdown = resolved_networking_path.read_text(encoding="utf-8")
    title = extract_title(report_markdown=report_markdown, fallback_stem=report_path.stem)

    rows = parse_report_rows(report_markdown)
    company_sections, introduction_messages = parse_networking_markdown(networking_markdown)
    detail_directory = build_detail_directory(resolved_output_path)
    detail_directory.mkdir(parents=True, exist_ok=True)

    detail_links: dict[tuple[str, str, str], str] = {}
    for row in rows:
        detail_filename = build_detail_filename(row)
        detail_path = detail_directory / detail_filename
        relative_link = detail_path.relative_to(resolved_output_path.parent).as_posix()
        detail_links[(row.decision, row.priority, row.company, row.role)] = relative_link

        company_section = company_sections.get(row.company)
        company_messages = [
            message
            for (company, _), message in introduction_messages.items()
            if company == row.company
        ]
        detail_html = render_contact_detail_page(
            row=row,
            title=title,
            company_section=company_section,
            company_messages=company_messages,
            backlink=f"../{resolved_output_path.name}",
        )
        detail_path.write_text(detail_html, encoding="utf-8")

    landing_html = render_landing_page(title, report_markdown, detail_links)
    resolved_output_path.write_text(landing_html, encoding="utf-8")
    return resolved_output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a daily job search report and networking matches as HTML.")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--networking", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    resolved_output_path = write_html_report(
        report_path=args.report,
        networking_path=args.networking,
        output_path=args.out,
    )
    print(f"Wrote HTML: {resolved_output_path}")


if __name__ == "__main__":
    main()
