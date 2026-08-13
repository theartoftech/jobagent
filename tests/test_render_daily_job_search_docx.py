from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from scripts.render_daily_job_search_docx import (
    SUMMARY_BOOKMARKS,
    SUMMARY_TOP_BOOKMARK,
    build_default_paths_docx,
    build_row_bookmark_name,
    parse_report_sections,
    write_docx_report,
)
from scripts.render_daily_job_search_html import parse_report_rows


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


SAMPLE_REPORT = "\n".join(
    [
        "# 2026-06-23 - Daily Job Search",
        "",
        "Discovery inputs:",
        "- `outputs/2026-06-23-discovered-jobs.json`",
        "- `outputs/2026-06-23-ats-discovery.md`",
        "",
        "## Apply",
        "",
        "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
        "|---|---|---|---|---|---|---|---|",
        "| 1 | Example Co | Senior Engineer | 2026-06-22 | Remote / Remote | 88 | https://example.com/jobs/1 | No |",
        "",
        "## Maybe",
        "",
        "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
        "|---|---|---|---|---|---|---|---|",
        "| 1 | Another Co | Staff Engineer | 2026-06-20 | Atlanta / Hybrid | 71 | https://example.com/jobs/2 | Yes (2026-06-21) |",
        "",
        "## Skip",
        "",
        "- 2 jobs older than 30 days",
        "",
        "## Gap Questions for Candidate",
        "",
        "- None from this pass.",
        "",
        "## Notes",
        "",
        "- Discovery completed successfully today.",
        "",
        "<!-- networking-pass:start -->",
        "## People to Contact",
        "- Hidden",
        "<!-- networking-pass:end -->",
    ]
)

SAMPLE_NETWORKING = "\n".join(
    [
        "# Networking Matches - 2026-06-23",
        "",
        "## People to Contact",
        "",
        "### Example Co",
        "",
        "Relevant jobs:",
        "- Apply: Senior Engineer; 2026-06-22; Remote / Remote; fit 88; https://example.com/jobs/1",
        "",
        "| Priority | Name | Current Company | Current Role | LinkedIn | Connected On | Match Source | Why Reach Out |",
        "|---:|---|---|---|---|---|---|---|",
        "| 1 | Jane Doe | Example Co | Recruiter | [Profile](https://linkedin.com/in/jane) | 23 Apr 2026 | export-confirmed | hiring-channel contact |",
        "",
        "### Another Co",
        "",
        "Relevant jobs:",
        "- Maybe: Staff Engineer; 2026-06-20; Atlanta / Hybrid; fit 71; https://example.com/jobs/2",
        "",
        "No current-company LinkedIn contacts found.",
        "",
        "## Introduction Messages",
        "",
        "### Example Co - Jane Doe",
        "",
        "Hi Jane,",
        "",
        "Would you be open to helping?",
    ]
)


class RenderDailyJobSearchDocxTests(unittest.TestCase):
    def test_parse_report_sections_preserves_preamble_and_notes(self) -> None:
        sections = parse_report_sections(SAMPLE_REPORT.split("<!-- networking-pass:start -->", 1)[0].strip())

        self.assertIn("Discovery inputs:", sections.preamble_lines)
        self.assertIn("- Discovery completed successfully today.", sections.section_lines["Notes"])

    def test_write_docx_report_creates_internal_bookmarks_and_detail_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            report_path = tmp_path / "2026-06-23-daily-job-search.md"
            networking_path, output_path = build_default_paths_docx(report_path)

            report_path.write_text(SAMPLE_REPORT, encoding="utf-8")
            networking_path.write_text(SAMPLE_NETWORKING, encoding="utf-8")

            created_path = write_docx_report(report_path=report_path, networking_path=networking_path)

            self.assertEqual(output_path, created_path)
            self.assertTrue(created_path.exists())

            with zipfile.ZipFile(created_path) as archive:
                document_xml = archive.read("word/document.xml")
                rels_xml = archive.read("word/_rels/document.xml.rels")

            root = ET.fromstring(document_xml)
            rels_root = ET.fromstring(rels_xml)

            bookmark_names = {
                bookmark.attrib.get(f"{{{W_NS}}}name")
                for bookmark in root.findall(f".//{{{W_NS}}}bookmarkStart")
            }
            self.assertIn(SUMMARY_TOP_BOOKMARK, bookmark_names)
            self.assertIn(SUMMARY_BOOKMARKS["Apply"], bookmark_names)
            self.assertIn(SUMMARY_BOOKMARKS["Maybe"], bookmark_names)

            rows = parse_report_rows(SAMPLE_REPORT)
            detail_bookmark = build_row_bookmark_name(rows[0])
            self.assertIn(detail_bookmark, bookmark_names)

            anchor_values = {
                hyperlink.attrib.get(f"{{{W_NS}}}anchor")
                for hyperlink in root.findall(f".//{{{W_NS}}}hyperlink")
                if hyperlink.attrib.get(f"{{{W_NS}}}anchor")
            }
            self.assertIn(detail_bookmark, anchor_values)
            self.assertIn(SUMMARY_BOOKMARKS["Apply"], anchor_values)

            relationship_targets = {
                rel.attrib["Target"]
                for rel in rels_root.findall(f".//{{{PKG_REL_NS}}}Relationship")
                if rel.attrib.get("TargetMode") == "External"
            }
            self.assertIn("https://example.com/jobs/1", relationship_targets)
            self.assertIn("https://linkedin.com/in/jane", relationship_targets)

            text_content = "".join(root.itertext())
            self.assertIn("Who to Contact", text_content)
            self.assertIn("Jane Doe", text_content)
            self.assertIn("No current-company LinkedIn contacts found.", text_content)
            self.assertIn("Would you be open to helping?", text_content)
            self.assertIn("Gap Questions for Candidate", text_content)


if __name__ == "__main__":
    unittest.main()
