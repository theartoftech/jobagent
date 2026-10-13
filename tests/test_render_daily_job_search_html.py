from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.render_daily_job_search_html import (
    HumanSearchCompany,
    build_default_paths,
    build_detail_directory,
    build_detail_filename,
    build_linkedin_people_searches,
    extract_title,
    insert_detail_links,
    markdown_to_html,
    parse_networking_markdown,
    parse_report_rows,
    remove_networking_appendix,
    render_document,
    render_report_tabs,
    write_html_report,
)


SAMPLE_REPORT = "\n".join(
    [
        "# 2026-06-23 - Daily Job Search",
        "",
        "Discovery inputs:",
        "- `outputs/2026-06-23-discovered-jobs.json`",
        "",
        "## Discovery Candidates — Higher Signal",
        "",
        "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
        "|---|---|---|---|---|---|---|---|",
        "| 1 | Example Co | Senior Engineer | 2026-06-22 | Remote / Remote | 88 | https://example.com/jobs/1 | No |",
        "",
        "## Discovery Candidates — Review Signal",
        "",
        "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
        "|---|---|---|---|---|---|---|---|",
        "| 1 | Another Co | Staff Engineer | 2026-06-20 | Atlanta / Hybrid | 71 | https://example.com/jobs/2 | Yes (2026-06-21) |",
        "",
        "## Notes",
        "",
        "- Note",
        "",
        "## Human Search Calibration",
        "",
        "Manually search these company career sites and compare any roles found against this report.",
        "",
        "1. TriNet - https://jobs.trinet.com/careers?domain=trinet.com",
        "2. Riskonnect - https://riskonnect.com/company/careers/",
        "3. Collibra - https://www.collibra.com/company/careers",
        "4. Greenlight - https://greenlight.com/careers",
        "5. Stord - https://www.stord.com/careers",
        "",
        "<!-- networking-pass:start -->",
        "## LinkedIn Recruiter Add Notes",
        "",
        "### Example Co",
        "",
        "Hi, I am exploring Example Co roles including Senior Engineer.",
        "",
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
        "| 1 | Jane Doe | Example Co | Recruiter | [Profile](https://linkedin.com/in/jane) | 23 Apr 2026 | live-profile-confirmed | hiring-channel contact |",
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


class HumanSearchChecklistTests(unittest.TestCase):
    def test_landing_page_shows_manual_check_count_in_tab(self) -> None:
        html = render_report_tabs(
            title="2026-06-23 - Daily Job Search",
            jobs_html="<h1>Jobs</h1>",
            companies=[
                HumanSearchCompany(
                    name=f"Company {index}",
                    careers_url=f"https://company-{index}.example.com/careers",
                )
                for index in range(1, 6)
            ],
        )

        self.assertIn(">Companies to Check (5)</button>", html)
        self.assertIn("0 of 5 searched", html)


class RenderDailyJobSearchHtmlTests(unittest.TestCase):
    def test_remove_networking_appendix_strips_marker_block(self) -> None:
        cleaned = remove_networking_appendix(SAMPLE_REPORT)

        self.assertNotIn("Hidden", cleaned)
        self.assertIn("## Notes", cleaned)
        self.assertIn("## LinkedIn Recruiter Add Notes", cleaned)
        self.assertIn("Hi, I am exploring Example Co roles", cleaned)

    def test_markdown_to_html_renders_heading_list_table_and_link(self) -> None:
        markdown = "\n".join(
            [
                "# Title",
                "",
                "- First item",
                "",
                "| Company | URL |",
                "|---|---|",
                "| Example | [Job](details/example.html) |",
            ]
        )

        html = markdown_to_html(markdown)

        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<li>First item</li>", html)
        self.assertIn("<table>", html)
        self.assertIn('<a href="details/example.html">Job</a>', html)

    def test_markdown_to_html_highlights_unapplied_job_rows(self) -> None:
        markdown = "\n".join(
            [
                "| Priority | Company | Role | Already Applied |",
                "|---|---|---|---|",
                "| 1 | Example Co | Senior Engineer | No |",
                "| 2 | Applied Co | Staff Engineer | Yes (2026-07-17) |",
            ]
        )

        html = markdown_to_html(markdown)

        self.assertIn('<tr class="needs-application">', html)
        self.assertEqual(1, html.count('class="needs-application"'))

    def test_render_document_uses_full_width_layout(self) -> None:
        html = render_document("Report", "<h1>Report</h1>")

        self.assertIn("max-width: none;", html)
        self.assertIn("width: 100%;", html)
        self.assertNotIn("max-width: 980px", html)

    def test_companion_link_accepts_report_title_with_trailing_date(self) -> None:
        report = SAMPLE_REPORT.replace(
            "# 2026-06-23 - Daily Job Search",
            "# Daily Job Search Report - 2026-06-23",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            report_path = root / "2026-06-23-daily-job-search.md"
            networking_path = root / "2026-06-23-networking-matches.md"
            report_path.write_text(report, encoding="utf-8")
            networking_path.write_text(SAMPLE_NETWORKING, encoding="utf-8")
            rendered = write_html_report(
                report_path=report_path,
                networking_path=networking_path,
            ).read_text(encoding="utf-8")

        self.assertIn('href="2026-06-23-workflow-review.html"', rendered)

    def test_build_linkedin_people_searches_targets_recruiters_and_hiring_managers(self) -> None:
        row = parse_report_rows(SAMPLE_REPORT)[0]

        searches = build_linkedin_people_searches(row)

        labels = [search.label for search in searches]
        self.assertIn("Recruiters / Talent Acquisition", labels)
        self.assertIn("Engineering Hiring Managers", labels)
        self.assertIn("Role-Specific Technical Leads", labels)
        self.assertTrue(all(search.url.startswith("https://www.linkedin.com/search/results/people/?keywords=") for search in searches))
        self.assertTrue(any("Example%20Co" in search.url for search in searches))
        self.assertTrue(any("Senior%20Engineer" in search.url for search in searches))

    def test_parse_networking_markdown_handles_escaped_pipes_in_contact_rows(self) -> None:
        networking = "\n".join(
            [
                "# Networking",
                "",
                "## People to Contact",
                "",
                "### Example Co",
                "",
                "| Priority | Name | Current Company | Current Role | LinkedIn | Connected On | Match Source | Why Reach Out |",
                "|---:|---|---|---|---|---|---|---|",
                "| 1 | Jane Doe | Example Co | Director \\| Recruiting | [Profile](https://linkedin.com/in/jane) | 23 Apr 2026 | export-confirmed | hiring-channel contact |",
                "",
                "## Introduction Messages",
            ]
        )

        companies, _ = parse_networking_markdown(networking)

        self.assertEqual("Director | Recruiting", companies["Example Co"].contacts[0].current_role)

    def test_extract_title_prefers_first_heading(self) -> None:
        title = extract_title(
            report_markdown=SAMPLE_REPORT,
            fallback_stem="2026-06-23-daily-job-search",
        )

        self.assertEqual("2026-06-23 - Daily Job Search", title)

    def test_parse_report_rows_extracts_apply_and_maybe_rows(self) -> None:
        rows = parse_report_rows(SAMPLE_REPORT)

        self.assertEqual(2, len(rows))
        self.assertEqual("Apply", rows[0].decision)
        self.assertEqual("Example Co", rows[0].company)
        self.assertEqual("Another Co", rows[1].company)
        self.assertEqual("Yes (2026-06-21)", rows[1].already_applied)

    def test_parse_networking_markdown_extracts_contacts_and_messages(self) -> None:
        companies, messages = parse_networking_markdown(SAMPLE_NETWORKING)

        self.assertIn("Example Co", companies)
        self.assertEqual(1, len(companies["Example Co"].contacts))
        self.assertEqual(
            "No current-company LinkedIn contacts found.",
            companies["Another Co"].no_contacts_message,
        )
        self.assertIn(("Example Co", "Jane Doe"), messages)

    def test_insert_detail_links_adds_contacts_column_to_report_tables(self) -> None:
        linked = insert_detail_links(
            SAMPLE_REPORT,
            {
                ("Apply", "1", "Example Co", "Senior Engineer"): "pages/example.html",
                ("Maybe", "1", "Another Co", "Staff Engineer"): "pages/another.html",
            },
        )

        self.assertIn("| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |", linked)
        self.assertIn("Senior Engineer ([Contacts](pages/example.html))", linked)
        self.assertIn("Staff Engineer ([Contacts](pages/another.html))", linked)

    def test_insert_detail_links_preserves_escaped_pipes_in_job_title(self) -> None:
        report = SAMPLE_REPORT.replace(
            "Senior Engineer",
            "Senior Software Engineer - k6 Core \\| USA, EST only \\| Remote",
            1,
        )
        linked = insert_detail_links(
            report,
            {
                (
                    "Apply",
                    "1",
                    "Example Co",
                    "Senior Software Engineer - k6 Core | USA, EST only | Remote",
                ): "pages/example.html",
                ("Maybe", "1", "Another Co", "Staff Engineer"): "pages/another.html",
            },
        )

        self.assertIn(
            "Senior Software Engineer - k6 Core \\| USA, EST only \\| Remote "
            "([Contacts](pages/example.html))",
            linked,
        )
        rendered = markdown_to_html(linked)
        self.assertIn(
            "<td>Senior Software Engineer - k6 Core | USA, EST only | Remote ",
            rendered,
        )
        self.assertEqual(8, rendered.count("<td>") // 2)

    def test_write_html_report_creates_landing_page_and_detail_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            report_path = tmp_path / "2026-06-23-daily-job-search.md"
            networking_path, output_path = build_default_paths(report_path)

            report_path.write_text(SAMPLE_REPORT, encoding="utf-8")
            networking_path.write_text(SAMPLE_NETWORKING, encoding="utf-8")

            created_path = write_html_report(report_path=report_path, networking_path=networking_path)

            detail_dir = build_detail_directory(output_path)
            apply_detail = detail_dir / build_detail_filename(parse_report_rows(SAMPLE_REPORT)[0])
            maybe_detail = detail_dir / build_detail_filename(parse_report_rows(SAMPLE_REPORT)[1])

            landing_html = created_path.read_text(encoding="utf-8")
            self.assertEqual(created_path, output_path)
            self.assertIn("<title>2026-06-23 - Daily Job Search</title>", landing_html)
            self.assertIn("LinkedIn Recruiter Add Notes", landing_html)
            self.assertIn("Hi, I am exploring Example Co roles", landing_html)
            self.assertIn('role="tablist"', landing_html)
            self.assertIn('data-tab-target="jobs-panel"', landing_html)
            self.assertIn('data-tab-target="companies-panel"', landing_html)
            self.assertIn(
                'href="2026-06-23-workflow-review.html">Open Strict Workflow Review</a>',
                landing_html,
            )
            self.assertIn("Companies to Check", landing_html)
            self.assertEqual(5, landing_html.count('class="company-check"'))
            self.assertIn("0 of 5 searched", landing_html)
            self.assertIn("localStorage.getItem", landing_html)
            self.assertIn("localStorage.setItem", landing_html)
            self.assertIn("Reset checklist", landing_html)
            self.assertIn('<label class="company-name" for="company-check-1">TriNet</label>', landing_html)
            self.assertNotIn("<h2>Human Search Calibration</h2>", landing_html)
            self.assertIn('href="2026-06-23-daily-job-search-contacts/apply-1-example-co-senior-engineer.html"', landing_html)
            self.assertIn(">Contacts</a>", landing_html)
            self.assertTrue(apply_detail.exists())
            self.assertTrue(maybe_detail.exists())

            apply_html = apply_detail.read_text(encoding="utf-8")
            maybe_html = maybe_detail.read_text(encoding="utf-8")
            self.assertIn('href="../2026-06-23-daily-job-search.html"', apply_html)
            self.assertIn("Who to Contact", apply_html)
            self.assertIn("Jane Doe", apply_html)
            self.assertIn("Suggested Contact Messages", apply_html)
            self.assertIn("LinkedIn Searches", apply_html)
            self.assertIn("Recruiters / Talent Acquisition", apply_html)
            self.assertIn("Engineering Hiring Managers", apply_html)
            self.assertIn("Add Note for New LinkedIn Connections", apply_html)
            self.assertIn("I&#x27;m interested in the Senior Engineer role at Example Co", apply_html)
            self.assertRegex(apply_html, r"\d+ / 299 characters")
            self.assertIn("No current-company LinkedIn contacts found.", maybe_html)
            self.assertIn("live-profile-confirmed", apply_html)


if __name__ == "__main__":
    unittest.main()
