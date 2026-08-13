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


class SearchPlanCompactionTests(unittest.TestCase):
    def test_linkedin_feedback_targets_use_registry_manual_and_watchlist_lanes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = search_jobs.load_yaml(root / "ats_registry.yml")["companies"]
        manual = search_jobs.load_yaml(root / "manual_job_urls.yml")["jobs"]
        watchlist = search_jobs.load_yaml(root / "startup_watchlist.yml")["startups"]

        self.assertEqual(
            registry["OneTrust"]["ats"][0]["url"],
            "https://job-boards.greenhouse.io/onetrust",
        )
        self.assertEqual(
            registry["LexisNexis Risk Solutions"]["ats"][1]["url"],
            "https://relx.wd3.myworkdayjobs.com/LexisNexisLegal",
        )
        self.assertTrue(any(item.get("company") == "FullStack" for item in manual))
        manual_urls = {item.get("url") for item in manual}
        expected_linkedin_urls = {
            "https://careers.waystar.com/atlanta-ga/staff-software-engineer/B04A119A21D34D2495BAC1DC53164D60/job/",
            "https://ibqbjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/job/154498/",
            "https://job-boards.greenhouse.io/iterable/jobs/7766310",
            "https://jobs.jobvite.com/appfolio/job/ot7aAfw4",
            "https://autostore.wd3.myworkdayjobs.com/autostore/job/Atlanta-GA-USA/Principal-Architect_JR102626",
            "https://careers.truist.com/us/en/job/TBJTBFUSR0117326EXTERNALENUS/Lead-Software-Engineer",
            "https://careers.circle.com/us/en/job/CIICIRUSJR100014EGEXTERNALENUS/Staff-Software-Engineer",
            "https://ejep.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/job/3388/",
        }
        self.assertTrue(expected_linkedin_urls.issubset(manual_urls))
        manual_by_url = {item.get("url"): item for item in manual}
        self.assertTrue(
            all(
                str(manual_by_url[url].get("source_url", "")).startswith(
                    "https://www.linkedin.com/jobs/view/"
                )
                for url in expected_linkedin_urls
            )
        )
        self.assertTrue(any(item.get("name") == "FullStack" for item in watchlist))

    def test_trinet_is_configured_as_a_target_company(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companies = search_jobs.normalize_company_entries(search_jobs.load_yaml(root / "companies.yml"))
        matches = [company for company in companies if company["name"] == "TriNet"]

        self.assertEqual(1, len(matches))
        self.assertEqual("https://jobs.trinet.com/careers?domain=trinet.com", matches[0]["careers_url"])
        self.assertIn("payroll", matches[0]["notes"].casefold())

    def test_indeed_feedback_is_preserved_for_report_intake(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manual = search_jobs.load_yaml(root / "manual_job_urls.yml")["jobs"]
        manual_by_url = {item.get("url"): item for item in manual}
        url = "https://www.indeed.com/viewjob?jk=aa89b4d0151e7b12"

        self.assertIn(url, manual_by_url)
        self.assertEqual(url, manual_by_url[url]["source_url"])
        self.assertEqual("indeed-manual-feedback", manual_by_url[url]["source"])

    def test_inspira_adp_board_and_indeed_find_are_configured(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companies = search_jobs.normalize_company_entries(search_jobs.load_yaml(root / "companies.yml"))
        by_name = {company["name"]: company for company in companies}
        registry = search_jobs.load_yaml(root / "ats_registry.yml")["companies"]
        manual = search_jobs.load_yaml(root / "manual_job_urls.yml")["jobs"]
        manual_by_url = {item.get("url"): item for item in manual}
        job_url = "https://myjobs.adp.com/inspiracareers/cx/job-details?reqId=5001215356506"

        self.assertEqual(
            "https://myjobs.adp.com/inspiracareers/",
            by_name["Inspira Financial"]["careers_url"],
        )
        self.assertEqual(
            "https://myjobs.adp.com/inspiracareers/",
            registry["Inspira Financial"]["ats"][0]["url"],
        )
        self.assertEqual("Lead Solutions Architect (Remote)", manual_by_url[job_url]["title"])
        self.assertEqual(
            "https://www.indeed.com/viewjob?jk=538c32ce70d87a9b",
            manual_by_url[job_url]["source_url"],
        )

    def test_linkedin_forward_deployed_findings_are_preserved_for_report_intake(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manual = search_jobs.load_yaml(root / "manual_job_urls.yml")["jobs"]
        manual_by_url = {item.get("url"): item for item in manual}

        c3_url = "https://c3.ai/job-description/8577794002?gh_jid=8577794002"
        global_logic_url = "https://www.linkedin.com/jobs/view/4449757371/"

        self.assertEqual(
            "https://www.linkedin.com/jobs/view/4420151486/",
            manual_by_url[c3_url]["source_url"],
        )
        self.assertEqual("C3 AI", manual_by_url[c3_url]["company"])
        self.assertEqual(
            "needs_official_verification",
            manual_by_url[global_logic_url]["verification_status"],
        )
        profile = search_jobs.load_yaml(root / "candidate_profile.yml")["target_profile"]
        self.assertIn("Forward Deployed Team Lead", profile["title_targets"])

    def test_linkedin_is_primary_and_profile_has_expanded_search_policy(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sources = search_jobs.load_yaml(root / "job_sources.yml")["sources"]
        linkedin = next(source for source in sources if source["name"] == "LinkedIn Jobs")
        profile = search_jobs.load_yaml(root / "candidate_profile.yml")["target_profile"]
        workflow = search_jobs.load_yaml(root / "candidate_workflow_profile.yml")
        rules = (root / "search_rules.md").read_text(encoding="utf-8")

        self.assertEqual("primary_browser_assisted", linkedin["type"])
        self.assertEqual(1, linkedin["priority"])
        for title in (
            "Staff Java Engineer",
            "Principal Java Engineer",
            "Cloud Architect",
            "Technical Product Manager",
        ):
            self.assertIn(title, profile["title_targets"])
        self.assertIn("Oracle", profile["excluded_companies"])
        self.assertIn("Example City", profile["locations"]["preferred"])
        self.assertIn("Nearby City", profile["locations"]["preferred"])
        self.assertEqual(150000, profile["compensation"]["target_minimum"])
        self.assertEqual(150000, workflow["salary_floor"])
        for action in (
            "Apply Now",
            "Apply After Resume Tailoring",
            "Investigate Further",
            "Network First",
            "Skip",
        ):
            self.assertIn(action, rules)
        self.assertIn("generic email", rules.casefold())
        self.assertIn("third-party recruiter", rules.casefold())

    def test_friend_recommended_companies_are_configured(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companies = search_jobs.normalize_company_entries(search_jobs.load_yaml(root / "companies.yml"))
        by_name = {company["name"]: company for company in companies}

        self.assertEqual("https://riskonnect.com/company/careers/", by_name["Riskonnect"]["careers_url"])
        self.assertIn("Atlanta", by_name["Riskonnect"]["notes"])
        self.assertEqual("https://www.collibra.com/company/careers", by_name["Collibra"]["careers_url"])
        self.assertIn("data governance", by_name["Collibra"]["notes"].casefold())

        registry = search_jobs.load_yaml(root / "ats_registry.yml")
        self.assertEqual(
            "https://job-boards.greenhouse.io/collibra",
            registry["companies"]["Collibra"]["ats"][0]["url"],
        )

    def test_northeast_georgia_health_system_is_configured(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companies = search_jobs.normalize_company_entries(search_jobs.load_yaml(root / "companies.yml"))
        by_name = {company["name"]: company for company in companies}

        nghs = by_name["Northeast Georgia Health System"]
        self.assertEqual("https://www.nghs.com/careers", nghs["careers_url"])
        self.assertIn("healthcare", nghs["notes"].casefold())
        self.assertIn("integration", nghs["notes"].casefold())

        registry = search_jobs.load_yaml(root / "ats_registry.yml")
        self.assertEqual(
            "https://nghs.wd1.myworkdayjobs.com/External",
            registry["companies"]["Northeast Georgia Health System"]["ats"][0]["url"],
        )

    def test_write_search_plan_caps_detailed_company_sections(self) -> None:
        companies = [
            {
                "name": f"Company {index}",
                "careers_url": f"https://company{index}.example.com/careers",
                "notes": "Important company notes.",
            }
            for index in range(search_jobs.MAX_DETAILED_COMPANY_SECTIONS_IN_PLAN + 5)
        ]
        profile = {
            "title_targets": ["Senior Software Engineer", "Solution Architect"],
            "strong_keywords": ["java", "spring"],
            "acceptable_secondary_keywords": ["sql"],
            "exclude_if_primary_focus": ["python"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = search_jobs.write_search_plan(
                today="2026-06-25",
                companies=companies,
                profile=profile,
                rules_text="Keep it tight.",
                out_dir=Path(temp_dir),
            )
            content = output_path.read_text(encoding="utf-8")

        self.assertLessEqual(
            content.count("\n### Company "),
            search_jobs.MAX_DETAILED_COMPANY_SECTIONS_IN_PLAN,
        )
        self.assertIn("Additional companies not expanded in this plan", content)
        self.assertIn("Run sharded ATS/company discovery first", content)
        self.assertIn("scripts/jobagent_python.sh scripts/discover_jobs_sharded.py", content)


if __name__ == "__main__":
    unittest.main()
