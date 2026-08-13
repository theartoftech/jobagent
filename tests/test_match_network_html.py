from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import match_network


class MatchNetworkHtmlTests(unittest.TestCase):
    def test_default_linkedin_export_is_local_and_generic(self) -> None:
        self.assertEqual(Path("linkedin-export.zip"), match_network.DEFAULT_LINKEDIN_EXPORT)

    def test_parse_recommended_jobs_accepts_discovery_candidate_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            report_path = Path(tmp_dir) / "2026-07-23-daily-job-search.md"
            report_path.write_text(
                "\n".join(
                    [
                        "# Daily Job Search Report - 2026-07-23",
                        "",
                        "## Discovery Candidates — Higher Signal",
                        "",
                        "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
                        "|---|---|---|---|---|---|---|---|",
                        "| 1 | ExampleCo | Senior Engineer | 2026-07-23 | Remote | 88 | https://example.com/1 | No |",
                    ]
                ),
                encoding="utf-8",
            )

            jobs = match_network.parse_recommended_jobs(report_path)

        self.assertEqual(1, len(jobs))
        self.assertEqual("Apply", jobs[0].decision)

    def test_intro_message_dedupes_titles_and_omits_decision_labels(self) -> None:
        contact = match_network.Contact(
            first_name="Mila",
            last_name="Crain",
            linkedin_url="https://www.linkedin.com/in/milacrain",
            email="",
            company="NCR Voyix",
            position="Global Talent Acquisition Manager",
            connected_on="08 Nov 2010",
        )
        jobs = [
            match_network.Job(
                decision="Apply",
                company="NCR Voyix",
                title="Senior Software Engineer",
                posted="2026-06-09",
                location="ATLANTA, GA, USA / Hybrid",
                fit="86",
                url="https://example.com/jobs/1",
            ),
            match_network.Job(
                decision="Apply",
                company="NCR Voyix",
                title="Senior Software Engineer",
                posted="2026-06-11",
                location="ROGERS, AR, USA / Remote",
                fit="73",
                url="https://example.com/jobs/2",
            ),
            match_network.Job(
                decision="Apply",
                company="NCR Voyix",
                title="Principal Software Engineer",
                posted="2026-06-17",
                location="ATLANTA, GA, USA / Hybrid",
                fit="72",
                url="https://example.com/jobs/3",
            ),
        ]

        message = match_network.intro_message(contact, "NCR Voyix", jobs)

        self.assertIn(
            "I am looking at a couple of NCR Voyix roles, including Senior Software Engineer and Principal Software Engineer",
            message,
        )
        self.assertNotIn("(Apply)", message)
        self.assertEqual(message.count("Senior Software Engineer"), 1)

    def test_recruiter_add_notes_include_only_unapplied_apply_jobs(self) -> None:
        jobs = [
            match_network.Job(
                decision="Apply",
                company="ExampleCo",
                title="Senior Software Engineer",
                posted="2026-06-23",
                location="Remote / Remote",
                fit="88",
                url="https://example.com/jobs/1",
                already_applied="No",
            ),
            match_network.Job(
                decision="Apply",
                company="ExampleCo",
                title="Principal Software Engineer",
                posted="2026-06-23",
                location="Remote / Remote",
                fit="86",
                url="https://example.com/jobs/2",
                already_applied="Yes (2026-07-17)",
            ),
            match_network.Job(
                decision="Maybe",
                company="MaybeCo",
                title="Staff Software Engineer",
                posted="2026-06-23",
                location="Remote / Remote",
                fit="72",
                url="https://example.com/jobs/3",
                already_applied="No",
            ),
        ]

        markdown = match_network.build_networking_markdown(
            today=match_network.dt.date(2026, 6, 23),
            jobs=jobs,
            matches={"ExampleCo": [], "MaybeCo": []},
            linkedin_export=Path("Connections.csv"),
        )

        add_notes_section = markdown.split("## People to Contact", 1)[0]
        self.assertIn("## LinkedIn Recruiter Add Notes", markdown)
        self.assertIn("### ExampleCo", add_notes_section)
        self.assertIn("Senior Software Engineer", add_notes_section)
        self.assertNotIn("Principal Software Engineer", add_notes_section)
        self.assertNotIn("### MaybeCo", add_notes_section)

    def test_run_writes_html_report_alongside_markdown_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            report_path = tmp_path / "2026-06-23-daily-job-search.md"
            out_dir = tmp_path
            report_path.write_text(
                "\n".join(
                    [
                        "# 2026-06-23 - Daily Job Search",
                        "",
                        "## Apply",
                        "",
                        "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
                        "|---|---|---|---|---|---|---|---|",
                        "| 1 | ExampleCo | Senior Software Engineer | 2026-06-23 | Remote / Remote | 80 | https://example.com/jobs/1 | No |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            sample_job = match_network.Job(
                decision="Apply",
                company="ExampleCo",
                title="Senior Software Engineer",
                posted="2026-06-23",
                location="Remote / Remote",
                fit="80",
                url="https://example.com/jobs/1",
            )

            with (
                mock.patch.object(match_network, "read_connections", return_value=[]),
                mock.patch.object(match_network, "parse_recommended_jobs", return_value=[sample_job]),
                mock.patch.object(match_network, "match_contacts", return_value={"ExampleCo": []}),
            ):
                markdown_path = match_network.run(
                    linkedin_export=tmp_path / "Connections.csv",
                    linkedin_live_verifications=None,
                    daily_report=report_path,
                    out_dir=out_dir,
                    today=match_network.dt.date(2026, 6, 23),
                    update_report=True,
                )

            html_path = tmp_path / "2026-06-23-daily-job-search.html"
            self.assertEqual(markdown_path.name, "2026-06-23-networking-matches.md")
            self.assertTrue(html_path.exists())
            rendered = html_path.read_text(encoding="utf-8")
            self.assertIn("<title>2026-06-23 - Daily Job Search</title>", rendered)
            self.assertIn("ExampleCo", rendered)


if __name__ == "__main__":
    unittest.main()
