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
    def test_salesforce_family_has_distinct_shared_board_searches(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = search_jobs.load_yaml(root / "ats_registry.yml")["companies"]
        companies = search_jobs.normalize_company_entries(search_jobs.load_yaml(root / "companies.yml"))
        by_name = {company["name"]: company for company in companies}

        for company_name, search_term in (
            ("Salesforce", "Principal%20Software%20Engineer"),
            ("MuleSoft", "MuleSoft"),
            ("Slack", "Slack"),
        ):
            registry_url = registry[company_name]["ats"][0]["url"]
            self.assertIn("salesforce.com/company/careers/jobs/", registry_url)
            self.assertIn(f"search={search_term}", registry_url)
            self.assertEqual(by_name[company_name]["human_search_url"], registry_url + "#results")

    def test_cognizant_has_first_party_registry_mapping(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = search_jobs.load_yaml(root / "ats_registry.yml")["companies"]

        self.assertEqual(
            registry["Cognizant"]["ats"][0]["url"],
            "https://careers.cognizant.com/us-en/jobs/",
        )

    def test_ibm_uses_canonical_avature_search_and_preserves_manual_regression(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = search_jobs.load_yaml(root / "ats_registry.yml")["companies"]
        manual = search_jobs.load_yaml(root / "manual_job_urls.yml")["jobs"]

        self.assertEqual(
            registry["IBM"]["ats"][0]["url"],
            "https://careers.ibm.com/en_US/careers/SearchJobs",
        )
        ibm_jobs = [entry for entry in manual if entry.get("company") == "IBM"]
        self.assertTrue(any(str(entry.get("job_id")) == "119683" for entry in ibm_jobs))

    def test_oracle_is_a_direct_target_and_linkedin_findings_are_preserved(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companies = search_jobs.normalize_company_entries(search_jobs.load_yaml(root / "companies.yml"))
        by_name = {company["name"]: company for company in companies}
        manual = search_jobs.load_yaml(root / "manual_job_urls.yml")["jobs"]
        oracle_jobs = {str(entry.get("job_id")): entry for entry in manual if entry.get("company") == "Oracle"}

        self.assertEqual(
            "https://careers.oracle.com/jobs/",
            by_name["Oracle"]["careers_url"],
        )
        self.assertIn("OCI", by_name["Oracle"]["notes"])
        self.assertEqual({"341620", "338426", "338454"}, set(oracle_jobs))
        for entry in oracle_jobs.values():
            self.assertTrue(str(entry["url"]).startswith("https://eeho.fa.us2.oraclecloud.com/"))
            self.assertTrue(str(entry["source_url"]).startswith("https://www.linkedin.com/jobs/view/"))
            self.assertEqual("linkedin-manual-feedback", entry["source"])
            self.assertEqual("official_live", entry["verification_status"])

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
        self.assertNotIn("Oracle", profile["excluded_companies"])
        self.assertNotIn("Oracle", workflow["excluded_companies"])
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

    def test_daily_cover_letter_instructions_require_approved_writing_voice(self) -> None:
        root = Path(__file__).resolve().parents[1]
        daily_prompt = (root / "daily_job_search_prompt.md").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")

        self.assertIn("$jhaynes-writing-voice", daily_prompt)
        self.assertIn("jhaynes-writing-voice", readme)

    def test_ai_apply_is_read_only_verified_discovery_input(self) -> None:
        root = Path(__file__).resolve().parents[1]
        daily_prompt = (root / "daily_job_search_prompt.md").read_text(encoding="utf-8")
        rules = (root / "search_rules.md").read_text(encoding="utf-8")

        for text in (daily_prompt, rules):
            self.assertIn("AI Apply MCP", text)
            self.assertIn("official company or ATS posting", text)
            self.assertIn("queue_job_for_review", text)
            self.assertIn("submit_application", text)
            self.assertIn("AI Apply internal job ID", text)

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

    def test_oracle_cpq_customer_targets_are_configured(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companies = search_jobs.normalize_company_entries(search_jobs.load_yaml(root / "companies.yml"))
        by_name = {company["name"]: company for company in companies}
        registry = search_jobs.load_yaml(root / "ats_registry.yml")["companies"]
        expected = {
            "Vertiv": (
                "https://www.vertiv.com/en-us/about/career-center/",
                "https://www.oracle.com/customers/vertiv/",
                "https://egup.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/requisitions/",
            ),
            "DNV": (
                "https://jobs.dnv.com/",
                "https://www.oracle.com/customers/dnv-cpq/",
                "https://jobs.dnv.com/",
            ),
            "Aon": (
                "https://jobs.aon.com/jobs",
                "https://www.oracle.com/customers/aon-cx/",
                "https://jobs.aon.com/jobs",
            ),
            "Cloud Software Group": (
                "https://careers.cloud.com/jobs/search",
                "https://www.oracle.com/customers/cloud-software-group/",
                "https://careers.cloud.com/jobs/search",
            ),
        }

        for company_name, (careers_url, oracle_story_url, registry_url) in expected.items():
            self.assertIn(company_name, by_name)
            self.assertEqual(careers_url, by_name[company_name]["careers_url"])
            self.assertEqual(oracle_story_url, by_name[company_name]["oracle_customer_url"])
            self.assertIn("CPQ", by_name[company_name]["notes"])
            self.assertEqual(registry_url, registry[company_name]["ats"][0]["url"])

    def test_oracle_cpq_consulting_targets_are_configured(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companies = search_jobs.normalize_company_entries(search_jobs.load_yaml(root / "companies.yml"))
        by_name = {company["name"]: company for company in companies}
        registry = search_jobs.load_yaml(root / "ats_registry.yml")["companies"]
        expected = {
            "KPMG": "https://www.kpmguscareers.com/job-search/",
            "Huron": "https://huron.wd1.myworkdayjobs.com/huroncareers",
            "PwC": "https://jobs.us.pwc.com/oracle-jobs",
            "Accenture": "https://www.accenture.com/us-en/careers",
            "HCLTech": "https://careers.hcltech.com/go/United-States-Experienced/9557855/",
            "Infosys": "https://www.infosys.com/careers/americas/apply.html",
            "Deloitte": "https://apply.deloitte.com/en_US/careers",
            "LTM Limited": "https://careers.ltm.com/viewalljobs/",
            "Capgemini": "https://www.capgemini.com/careers/join-capgemini/job-search/",
            "Mythics": "https://external-mythics.icims.com/jobs/search?hashed=-435768531&ss=1",
            "Logicom": "https://www.logicom.net/careers/head-office/vacancies-list/",
            "Ataway": "https://atawayglobal.catsone.com/careers/97420/",
        }

        for company_name, registry_url in expected.items():
            self.assertIn(company_name, by_name)
            self.assertIn("Oracle", by_name[company_name]["notes"])
            self.assertEqual(registry_url, registry[company_name]["ats"][0]["url"])

        self.assertNotIn("oracle_partner_url", by_name["Ataway"])
        self.assertEqual(
            "https://partner-finder.oracle.com/?page=shell&shell=partner-finder&partner-finder=partner-finder-profile&CompanyNumber=4340253&filterValueStore=search_CPQ",
            by_name["LTM Limited"]["oracle_partner_url"],
        )

    def test_strategic_us_oracle_cpq_partners_are_configured(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companies = search_jobs.normalize_company_entries(search_jobs.load_yaml(root / "companies.yml"))
        by_name = {company["name"]: company for company in companies}
        registry = search_jobs.load_yaml(root / "ats_registry.yml")["companies"]
        expected = {
            "Tech Mahindra": ("https://careers.techmahindra.com/currentopportunity.aspx", "4133966"),
            "Wipro": ("https://careers.wipro.com/viewalljobs/?locale=en_US", "4134618"),
            "DXC Technology": ("https://careers.dxc.com/", "4-502001180139"),
            "Trinamix": ("https://www.trinamix.com/careers/", "51389615"),
            "Birlasoft": ("https://jobs.birlasoft.com/go/US/684844/", "12706842"),
            "Centroid Systems": ("https://centroid.applytojob.com/apply", "47391449"),
            "Inoapps": ("https://www.inoapps.com/join-inoapps/", "39309961"),
            "Argano": ("https://argano.com/careers/open-positions.html", "4-150622211257"),
            "Infovity": ("https://infovity.com/careers/", "50701523"),
            "Vigilant Technologies": ("https://vigilant-inc.com/careers/", "29059469"),
            "Zensar Technologies": ("https://www.zensar.com/careers", "PS203787-IN"),
            "NexInfo": ("https://nexinfo.com/careers/", "20587838"),
            "Jade Global": ("https://www.jadeglobal.com/careers", "10622368"),
            "CherryRoad Technologies": ("https://cherryroad.com/who-we-are/careers/", "22205066"),
            "Terillium": ("https://terillium.com/careers/", "30963307"),
            "Speridian Technologies": ("https://speridiantechnologies.applytojob.com/apply", "41821299"),
        }

        for company_name, (careers_url, partner_number) in expected.items():
            with self.subTest(company=company_name):
                self.assertIn(company_name, by_name)
                self.assertEqual(careers_url, by_name[company_name]["careers_url"])
                self.assertIn(f"CompanyNumber={partner_number}", by_name[company_name]["oracle_partner_url"])
                self.assertIn("CPQ", by_name[company_name]["notes"])
                self.assertEqual(careers_url, registry[company_name]["ats"][0]["url"])

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
