from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "search_jobs.py"
SPEC = importlib.util.spec_from_file_location("search_jobs", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {MODULE_PATH}")

search_jobs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = search_jobs
SPEC.loader.exec_module(search_jobs)


class SearchJobsPlanTests(unittest.TestCase):
    def test_human_search_rotation_prefers_company_specific_search_url(self) -> None:
        lines = search_jobs.format_human_search_companies(
            [
                {
                    "name": "Cognizant",
                    "careers_url": "https://careers.cognizant.com/us-en/jobs/",
                    "human_search_url": (
                        "https://careers.cognizant.com/us-en/jobs/"
                        "?keyword=Java&location=United%20States&cname=United%20States&pagesize=20#results"
                    ),
                }
            ]
        )

        self.assertEqual(
            lines,
            [
                "1. Cognizant - https://careers.cognizant.com/us-en/jobs/"
                "?keyword=Java&location=United%20States&cname=United%20States&pagesize=20#results"
            ],
        )

    def test_select_human_search_companies_uses_unsearched_companies_first(self) -> None:
        companies = [
            {"name": "Company A", "careers_url": "https://a.example.com"},
            {"name": "Company B", "careers_url": "https://b.example.com"},
            {"name": "Company C", "careers_url": "https://c.example.com"},
            {"name": "Company D", "careers_url": "https://d.example.com"},
            {"name": "Company E", "careers_url": "https://e.example.com"},
            {"name": "Company F", "careers_url": "https://f.example.com"},
        ]
        state = {"suggested_company_names": ["Company A"]}

        selected = search_jobs.select_human_search_companies(companies, state, count=5)

        self.assertEqual([company["name"] for company in selected], [
            "Company B",
            "Company C",
            "Company D",
            "Company E",
            "Company F",
        ])

    def test_select_human_search_companies_starts_new_cycle_after_all_companies_used(self) -> None:
        companies = [
            {"name": "Company A", "careers_url": "https://a.example.com"},
            {"name": "Company B", "careers_url": "https://b.example.com"},
        ]
        state = {"suggested_company_names": ["Company A", "Company B"]}

        selected = search_jobs.select_human_search_companies(companies, state, count=5)

        self.assertEqual([company["name"] for company in selected], ["Company A", "Company B"])

    def test_select_human_search_companies_fills_count_across_cycle_boundary(self) -> None:
        companies = [
            {"name": f"Company {letter}", "careers_url": f"https://{letter.casefold()}.example.com"}
            for letter in "ABCDEFG"
        ]
        state = {
            "suggested_company_names": [
                "Company A",
                "Company B",
                "Company C",
                "Company D",
                "Company E",
            ]
        }

        selected = search_jobs.select_human_search_companies(companies, state, count=5)
        updated = search_jobs.update_human_search_state(state, selected, companies)

        self.assertEqual(
            [company["name"] for company in selected],
            ["Company F", "Company G", "Company A", "Company B", "Company C"],
        )
        self.assertEqual(
            updated["suggested_company_names"],
            ["Company A", "Company B", "Company C"],
        )

    def test_write_human_search_rotation_updates_state_and_writes_dated_file(self) -> None:
        companies = [
            {"name": "Company A", "careers_url": "https://a.example.com/careers"},
            {"name": "Company B", "careers_url": "https://b.example.com/careers"},
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            selected = search_jobs.write_human_search_rotation(
                today="2026-07-07",
                companies=companies,
                out_dir=out_dir,
                count=1,
            )
            markdown = (out_dir / "2026-07-07-human-search-companies.md").read_text(encoding="utf-8")
            state = search_jobs.load_human_search_state(out_dir)

        self.assertEqual([company["name"] for company in selected], ["Company A"])
        self.assertIn("## Human Search Companies - 2026-07-07", markdown)
        self.assertIn("Company A", markdown)
        self.assertIn("https://a.example.com/careers", markdown)
        self.assertEqual(state["suggested_company_names"], ["Company A"])

    def test_write_search_plan_mentions_inspection_queue_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            for suffix in (
                "workflow-review.json",
                "workflow-review.html",
                "human-review-packets.md",
                "resume-ready-jobs.md",
                "resume-production.json",
                "applied-jobs.json",
            ):
                (out_dir / f"2026-06-27-{suffix}").write_text("test", encoding="utf-8")
            path = search_jobs.write_search_plan(
                today="2026-06-27",
                companies=[{"name": "Example Co", "careers_url": "https://example.com/jobs"}],
                profile={
                    "title_targets": ["Senior Software Engineer"],
                    "strong_keywords": ["Java"],
                    "acceptable_secondary_keywords": [],
                    "exclude_if_primary_focus": [],
                    "locations": {},
                },
                rules_text="Rules",
                out_dir=out_dir,
                human_search_companies=[
                    {"name": "Example Co", "careers_url": "https://example.com/jobs"}
                ],
            )

            markdown = path.read_text(encoding="utf-8")

        self.assertIn("scripts/jobagent_python.sh scripts/build_job_inspection_queue.py", markdown)
        self.assertIn("scripts/jobagent_python.sh scripts/discover_jobs_sharded.py", markdown)
        self.assertIn("--startup-watchlist startup_watchlist.yml", markdown)
        self.assertIn("--startup-count 15", markdown)
        self.assertIn("--rotation", markdown)
        self.assertIn("scripts/jobagent_python.sh scripts/review_job_fit.py", markdown)
        self.assertIn("scripts/jobagent_python.sh scripts/produce_resumes.py", markdown)
        self.assertIn("scripts/jobagent_python.sh scripts/publish_skill_gap_analytics.py", markdown)
        self.assertIn("http://127.0.0.1:8086/api/analytics/workflow-runs", markdown)
        self.assertIn("--ssh-host jobtracker-host", markdown)
        self.assertIn("reuse and link that prior artifact instead of regenerating it", markdown)
        self.assertIn("scripts/jobagent_python.sh scripts/mark_applied_jobs.py", markdown)
        self.assertIn("--ssh-host jobtracker-host", markdown)
        self.assertNotIn("192.0.2.10", markdown)
        self.assertIn("--applied-jobs ", markdown)
        self.assertIn("--applied-jobs-out ", markdown)
        self.assertLess(
            markdown.index("scripts/jobagent_python.sh scripts/build_job_inspection_queue.py"),
            markdown.index("scripts/jobagent_python.sh scripts/mark_applied_jobs.py"),
        )
        self.assertLess(
            markdown.index("scripts/jobagent_python.sh scripts/mark_applied_jobs.py"),
            markdown.index("scripts/jobagent_python.sh scripts/review_job_fit.py"),
        )
        self.assertLess(
            markdown.index("scripts/jobagent_python.sh scripts/review_job_fit.py"),
            markdown.index("scripts/jobagent_python.sh scripts/produce_resumes.py"),
        )
        self.assertLess(
            markdown.index("scripts/jobagent_python.sh scripts/produce_resumes.py"),
            markdown.index("scripts/jobagent_python.sh scripts/publish_skill_gap_analytics.py"),
        )
        self.assertIn("must not block or undo resume production", markdown)
        self.assertIn("resume-ready-jobs.md` file as the only queue for resume generation", markdown)
        self.assertIn("`## Gap Questions for Candidate`", markdown)
        self.assertIn(
            "Do not recommend creating a resume for any job that is not listed in `resume-ready-jobs.md`.",
            markdown,
        )
        self.assertIn("2026-06-27-workflow-review.json", markdown)
        self.assertIn("2026-06-27-workflow-review.html", markdown)
        self.assertIn("2026-06-27-human-review-packets.md", markdown)
        self.assertIn("2026-06-27-resume-ready-jobs.md", markdown)
        self.assertIn("2026-06-27-resume-production.json", markdown)
        self.assertIn("2026-06-27-applied-jobs.json", markdown)
        self.assertIn("strict workflow HTML is the primary human-facing report", markdown)
        self.assertIn("carry forward active jobs from the latest strict workflow review", markdown)
        self.assertIn("Discovery Candidates — Higher Signal", markdown)
        self.assertIn("Discovery Candidates — Review Signal", markdown)
        self.assertIn("manually surfaced official live job URLs", markdown)
        self.assertIn(
            "browser-reviewed job findings from LinkedIn, Indeed, or any other site",
            markdown,
        )
        self.assertIn("Needs Freshness Verification", markdown)
        self.assertIn("last 60 days", markdown)
        self.assertIn("7-day / 21-day / 60-day freshness", markdown)
        self.assertIn("manual_job_urls.yml", markdown)
        self.assertIn("ats_registry.yml", markdown)
        self.assertIn("USAJOBS", markdown)
        self.assertIn("USAJOBS_USER_AGENT", markdown)
        self.assertIn("AI Apply MCP", markdown)
        self.assertIn("queue_job_for_review", markdown)
        self.assertIn("submit_application", markdown)
        self.assertIn("Human Search Calibration", markdown)
        self.assertIn("Example Co", markdown)

    def test_write_report_template_includes_freshness_verification_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            path = search_jobs.write_report_template(
                today="2026-06-27",
                out_dir=out_dir,
                human_search_companies=[
                    {"name": "Example Co", "careers_url": "https://example.com/jobs"}
                ],
            )

            markdown = path.read_text(encoding="utf-8")

        self.assertIn("## Needs Freshness Verification", markdown)
        self.assertIn("## Gap Questions for Candidate", markdown)
        self.assertIn("| Priority | Company | Role | Public Freshness Signal |", markdown)
        self.assertIn("Carry forward active roles from the latest strict workflow review", markdown)
        self.assertIn("Remove roles from the active carry-forward set after jobtracker marks them as already applied", markdown)
        self.assertIn("manually surfaced official live job URLs", markdown)
        self.assertIn(
            "browser-reviewed job findings from LinkedIn, Indeed, or any other site",
            markdown,
        )
        self.assertIn("strict workflow HTML is the actionable report", markdown)
        self.assertIn("Discovery Candidates — Higher Signal", markdown)
        self.assertIn("Do not run Word resume or cover-letter generation", markdown)
        self.assertIn("## Human Search Calibration", markdown)
        self.assertIn("Example Co", markdown)

    def test_build_title_queries_includes_company_official_search_domains(self) -> None:
        queries = search_jobs.build_title_queries(
            {
                "name": "Insight Global Staffing",
                "careers_url": "https://jobs.insightglobal.com/",
                "official_search_domains": [
                    "jobs.insightglobal.com/find_a_job",
                    "insightglobal.com/jobs/find_a_job",
                ],
            },
            {
                "title_targets": ["Senior Software Engineer", "Technical Consultant"],
                "strong_keywords": ["Java"],
            },
        )

        self.assertTrue(any("site:jobs.insightglobal.com/find_a_job" in query for query in queries))
        self.assertTrue(any("site:insightglobal.com/jobs/find_a_job" in query for query in queries))

    def test_build_title_queries_includes_forward_deployed_search(self) -> None:
        queries = search_jobs.build_title_queries(
            {
                "name": "Platform Co",
                "careers_url": "https://platform.example.com/careers",
            },
            {
                "title_targets": [
                    "Forward Deployed Engineer",
                    "Forward Deployed Architect",
                ],
                "strong_keywords": ["Java"],
            },
        )

        self.assertTrue(any("Forward Deployed Engineer" in query for query in queries))
        self.assertTrue(any("Forward Deployed Architect" in query for query in queries))


if __name__ == "__main__":
    unittest.main()
