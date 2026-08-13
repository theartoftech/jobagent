from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.mark_applied_jobs import (
    DEFAULT_JOBTRACKER_SSH_HOST,
    ReportJob,
    TrackerJob,
    extract_requisition_id,
    load_tracker_jobs_snapshot,
    match_tracker_job,
    update_section,
    write_tracker_jobs_snapshot,
)


class MarkAppliedJobsTests(unittest.TestCase):
    def test_default_jobtracker_host_uses_ssh_alias(self) -> None:
        self.assertEqual("jobtracker-host", DEFAULT_JOBTRACKER_SSH_HOST)

    def test_update_section_marks_new_discovery_candidate_heading(self) -> None:
        lines = [
            "## Discovery Candidates — Higher Signal",
            "",
            "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL | Already Applied |",
            "|---|---|---|---|---|---|---|---|",
            "| 1 | ExampleCo | Senior Engineer | 2026-07-23 | Remote | 88 | https://example.com/jobs/123 | No |",
        ]
        tracker = TrackerJob(
            company="ExampleCo",
            title="Senior Engineer",
            status="applied",
            job_url="https://example.com/jobs/123",
            applied_date="2026-07-22",
        )

        updated = update_section(lines, "Discovery Candidates — Higher Signal", [tracker])

        self.assertIn("Yes (2026-07-22)", "\n".join(updated))

    def test_update_section_preserves_escaped_pipes_in_job_title(self) -> None:
        lines = [
            "## Discovery Candidates — Higher Signal",
            "",
            "| Priority | Company | Role | Posted | Location / Work Model | Fit | URL |",
            "|---|---|---|---|---|---|---|",
            "| 1 | Grafana Labs | Staff Software Engineer - Platform, Application Core Services \\| USA \\| Remote | 2026-07-29 | United States (Remote) / Remote | 81 | https://example.com/jobs/1 |",
        ]

        updated = update_section(lines, "Discovery Candidates — Higher Signal", [])
        rendered = "\n".join(updated)

        self.assertIn(
            "Staff Software Engineer - Platform, Application Core Services \\| USA \\| Remote",
            rendered,
        )
        self.assertIn("| 81 | https://example.com/jobs/1 | No |", rendered)

    def test_distinct_requisition_ids_do_not_fall_back_to_company_and_title(self) -> None:
        report_job = ReportJob(
            company="Block",
            title="Senior Software Engineer, Product Platform",
            posted="",
            location="New York",
            fit="88",
            url="https://block.xyz/careers/jobs/5239566008?gh_jid=5239566008",
        )
        tracker_job = TrackerJob(
            company="Block",
            title="Senior Software Engineer, Product Platform",
            status="applied",
            job_url="https://block.xyz/careers/jobs/5231964008?gh_jid=5231964008",
            applied_date="2026-07-22",
        )

        self.assertIsNone(match_tracker_job(report_job, [tracker_job]))

    def test_intuit_uses_the_final_posting_identifier_not_shared_job_family_segment(self) -> None:
        trust_and_safety = (
            "https://jobs.intuit.com/job/new-york/"
            "principal-software-engineer-trust-and-safety/27595/96922204080"
        )
        traffic = (
            "https://jobs.intuit.com/job/san-diego/"
            "staff-software-engineer-traffic/27595/98200474400"
        )

        self.assertEqual("96922204080", extract_requisition_id(trust_and_safety))
        self.assertEqual("98200474400", extract_requisition_id(traffic))

    def test_company_title_fallback_remains_available_without_requisition_ids(self) -> None:
        report_job = ReportJob(
            company="ExampleCo",
            title="Principal Engineer",
            posted="",
            location="Remote",
            fit="90",
            url="https://example.com/careers/principal-engineer",
        )
        tracker_job = TrackerJob(
            company="ExampleCo",
            title="Principal Engineer",
            status="interviewing",
            job_url="",
            applied_date="",
        )

        self.assertEqual(tracker_job, match_tracker_job(report_job, [tracker_job]))

    def test_tracker_snapshot_round_trip_preserves_application_evidence(self) -> None:
        jobs = [
            TrackerJob(
                company="ReadyCo",
                title="Senior Engineer",
                status="applied",
                job_url="https://example.com/jobs/123456",
                applied_date="2026-07-20",
            )
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "2026-07-22-applied-jobs.json"
            write_tracker_jobs_snapshot(path, jobs=jobs, ssh_host="jobtracker-host")
            loaded = load_tracker_jobs_snapshot(path)

        self.assertEqual(jobs, loaded)

    def test_tracker_snapshot_rejects_a_different_run_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "2026-07-21-applied-jobs.json"
            write_tracker_jobs_snapshot(path, jobs=[], ssh_host="jobtracker-host")

            with self.assertRaisesRegex(ValueError, "does not match workflow date"):
                load_tracker_jobs_snapshot(path, expected_date="2026-07-22")


if __name__ == "__main__":
    unittest.main()
