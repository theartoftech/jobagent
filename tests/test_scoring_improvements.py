from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "discover_jobs.py"
SPEC = importlib.util.spec_from_file_location("discover_jobs_scoring_improvements", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {MODULE_PATH}")

discover_jobs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = discover_jobs
SPEC.loader.exec_module(discover_jobs)


BASE_PROFILE = {
    "title_targets": ["Senior Software Engineer", "Technical Consultant"],
    "strong_keywords": ["java", "spring boot", "rest api", "sql"],
    "acceptable_secondary_keywords": [],
    "exclude_if_primary_focus": ["React-heavy frontend", "production AI/ML engineer"],
}


class ScoringImprovementTests(unittest.TestCase):

    def test_specialist_cloud_qualifications_override_generic_java_boosts(self) -> None:
        job = self.make_job(title='Cloud Architect', description='Requirements & Qualifications\nExperience designing enterprise multi-cloud architectures.\nSkills: Java, Spring Boot, REST API, SQL, cloud architecture, technical consulting.')
        profile = {**BASE_PROFILE, 'required_capability_evidence': [{'label': 'Enterprise multi-cloud architecture', 'terms': ['multi-cloud'], 'title_pattern': r'cloud.*architect', 'evidence_status': 'unconfirmed'}]}
        discover_jobs.score_job(job, profile, 60)
        self.assertEqual('Skip', job.decision)
        self.assertLessEqual(job.fit_score, 45)

    def test_general_cloud_experience_is_not_a_specialist_blocker(self) -> None:
        job = self.make_job(title='Cloud Architect', description='Requirements & Qualifications\nExperience designing solutions on a public cloud such as OCI.\nSkills: Java, Spring Boot, REST API, SQL, architecture.')
        profile = {**BASE_PROFILE, 'required_capability_evidence': [{'label': 'Enterprise multi-cloud architecture', 'terms': ['multi-cloud'], 'title_pattern': r'cloud.*architect', 'evidence_status': 'unconfirmed'}]}
        discover_jobs.score_job(job, profile, 60)
        self.assertNotEqual('Skip', job.decision)
    def test_unavailable_posting_cannot_be_boosted_back_to_apply(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description="Build Java Spring Boot REST API SQL backend services with architecture, technical consulting and professional services leadership.",
        )
        job.verification_status = "official_unavailable"
        discover_jobs.score_job(job, BASE_PROFILE, 60)
        self.assertEqual("Skip", job.decision)
        self.assertLessEqual(job.fit_score, 45)

    def test_unsupported_guidewire_role_is_skipped_despite_java_matches(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer, Guidewire BillingCenter",
            description="Configure and extend Guidewire BillingCenter using Java, REST APIs and SQL. What Will Our Ideal Candidate Have? 5+ years of Guidewire BillingCenter configuration and integration experience. Technical consulting, professional services, technical advisory, client delivery leadership and architecture.",
        )
        discover_jobs.score_job(job, {**BASE_PROFILE, "unsupported_primary_platforms": ["Guidewire"]}, 60)
        self.assertEqual("Skip", job.decision)
        self.assertLessEqual(job.fit_score, 45)
        self.assertIn("unsupported primary platform: Guidewire", job.risk_flags)

    def test_optional_guidewire_integration_does_not_block_java_role(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description="Build Java Spring Boot REST API SQL backend services with architecture and technical leadership. Familiarity with Guidewire for an optional integration is a plus.",
        )
        discover_jobs.score_job(job, {**BASE_PROFILE, "unsupported_primary_platforms": ["Guidewire"]}, 60)
        self.assertNotEqual("Skip", job.decision)
        self.assertNotIn("unsupported primary platform: Guidewire", job.risk_flags)

    def make_job(self, *, title: str, description: str) -> discover_jobs.JobPosting:
        return discover_jobs.JobPosting(
            company="Example Co",
            title=title,
            url="https://example.com/jobs/1",
            location="Remote - United States",
            work_model="Remote",
            posted_age_days=5,
            verification_status="official_live",
            description=description,
        )

    def test_positive_override_cannot_revive_unavailable_posting(self) -> None:
        job = self.make_job(title="Senior Software Engineer", description="Build Java APIs")
        job.verification_status = "official_unavailable"
        discover_jobs.score_job(job, BASE_PROFILE, 60)
        discover_jobs.apply_job_overrides([job], [{"company": "Example Co", "decision": "Apply", "fit_score": "100"}])
        self.assertEqual("Skip", job.decision)
        self.assertLessEqual(job.fit_score, 45)

    def test_old_manual_verification_is_not_current_live_evidence(self) -> None:
        job = discover_jobs.job_from_verified_manual_metadata(
            entry={"url": "https://example.com/jobs/1", "title": "Senior Software Engineer",
                   "description": "Build Java Spring Boot REST API SQL backend services", "verified_on": "2026-10-13",
                   "verification_status": "official_live", "location": "Remote US"},
            company={"name": "Example Co"}, profile=BASE_PROFILE,
            today=discover_jobs.dt.date(2026, 10, 14), max_age_days=60,
        )
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual("verification_required", job.verification_status)
        self.assertEqual("Skip", job.decision)
        discover_jobs.enrich_job_with_verified_manual_metadata(
            job, entry={"verification_status": "official_live", "verified_on": "2026-10-14"},
            company={"name": "Example Co"}, profile=BASE_PROFILE,
            today=discover_jobs.dt.date(2026, 10, 14), max_age_days=60,
        )
        self.assertEqual("verification_required", job.verification_status)

    def test_expiration_uses_run_date_instead_of_wall_clock(self) -> None:
        job = self.make_job(title="Senior Software Engineer", description="End Date: 2026-10-01 Build Java Spring Boot REST API SQL backend services")
        discover_jobs.score_job(job, BASE_PROFILE, 60, as_of=discover_jobs.dt.date(2026, 9, 30))
        self.assertNotEqual("Skip", job.decision)
        discover_jobs.score_job(job, BASE_PROFILE, 60, as_of=discover_jobs.dt.date(2026, 10, 14))
        self.assertEqual("Skip", job.decision)
        discover_jobs.apply_job_overrides([job], [{"company": "Example Co", "decision": "Apply", "fit_score": "100"}], as_of=discover_jobs.dt.date(2026, 10, 14))
        self.assertEqual("Skip", job.decision)

    def test_company_title_alias_counts_as_target_title_match(self) -> None:
        job = self.make_job(
            title="Engineer Senior",
            description="Java Spring Boot REST API SQL backend services",
        )
        company = {
            "name": "Example Co",
            "title_aliases": ["Engineer Senior"],
        }

        discover_jobs.score_job(job, BASE_PROFILE, 45, company=company)

        self.assertIn("target title match", job.fit_reasons)
        self.assertGreaterEqual(job.fit_score, 65)

    def test_semantic_director_title_variant_counts_as_target_title_match(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": ["Director of Software Engineering"],
        }
        job = self.make_job(
            title="Director, Software Engineering & Development",
            description="Lead Java platform engineering, REST APIs, and enterprise modernization.",
        )

        discover_jobs.score_job(job, profile, 45)

        self.assertIn("target title match", job.fit_reasons)
        self.assertGreaterEqual(job.fit_score, 65)

    def test_unrelated_director_title_does_not_count_as_target_title_match(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": ["Director of Software Engineering"],
        }
        job = self.make_job(
            title="Director, Product Marketing",
            description="Own positioning, campaigns, and market research.",
        )

        discover_jobs.score_job(job, profile, 45)

        self.assertNotIn("target title match", job.fit_reasons)


    def test_required_german_speaking_role_is_hard_rejected(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Solutions Engineer"],
            "unsupported_required_languages": ["German"],
        }
        job = self.make_job(
            title="Solutions Engineer, Enterprise (Pre-Sales) - German speaking",
            description=(
                "Lead technical discovery and design enterprise integrations using REST APIs, "
                "Java, Spring Boot, and distributed systems. Fluent German is required."
            ),
        )

        discover_jobs.score_job(job, profile, 60, company={"name": "Example Co"})

        self.assertEqual("Skip", job.decision)
        self.assertLessEqual(job.fit_score, 45)
        self.assertIn("required spoken language unsupported: German", job.risk_flags)


    def test_forward_deployed_role_with_language_alternatives_is_not_ai_or_stack_rejected(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Forward Deployed Team Lead"],
            "strong_keywords": [
                *BASE_PROFILE["strong_keywords"],
                "integration",
                "kubernetes",
                "docker",
            ],
        }
        job = self.make_job(
            title="Forward Deployed Team Lead",
            description=(
                "Own enterprise customers end-to-end through technical architecture, hands-on "
                "coding, REST APIs, integrations, and production deployment. Strong proficiency "
                "in Python, TypeScript, Java, Go, or C++. Work with Docker and Kubernetes. "
                "Preferred familiarity with AWS, Azure, or GCP AI platforms."
            ),
        )

        discover_jobs.score_job(job, profile, 60, company={"name": "C3 AI"})

        self.assertEqual("Apply", job.decision)
        self.assertNotIn("primary AI/ML platform role", job.risk_flags)
        self.assertFalse(any("hard-stack requirement gaps" in risk for risk in job.risk_flags))


    def test_non_atlanta_hybrid_role_is_hard_rejected(self) -> None:
        profile = {
            **BASE_PROFILE,
            "hybrid_allowed_metro_tokens": ["Atlanta", "Alpharetta", "Douglasville"],
        }
        job = discover_jobs.JobPosting(
            company="Mastercard",
            title="Senior Software Engineer",
            url="https://example.com/jobs/ofallon",
            location="O'Fallon, Missouri",
            work_model="Hybrid",
            posted_age_days=3,
            description="Java Spring Boot REST API SQL distributed systems",
        )

        discover_jobs.score_job(job, profile, 60)

        self.assertEqual("Skip", job.decision)
        self.assertLessEqual(job.fit_score, 45)
        self.assertIn("physical location outside candidate commute area", job.risk_flags)

    def test_atlanta_metro_hybrid_role_remains_eligible(self) -> None:
        profile = {
            **BASE_PROFILE,
            "hybrid_allowed_metro_tokens": ["Atlanta", "Alpharetta", "Douglasville"],
        }
        job = discover_jobs.JobPosting(
            company="Example Co",
            title="Senior Software Engineer",
            url="https://example.com/jobs/atlanta",
            location="Atlanta, Georgia",
            work_model="Hybrid",
            posted_age_days=3,
            description="Java Spring Boot REST API SQL distributed systems",
        )

        discover_jobs.score_job(job, profile, 60)

        self.assertNotEqual("Skip", job.decision)
        self.assertNotIn("physical location outside candidate commute area", job.risk_flags)

    def test_exclusion_penalty_is_not_applied_for_single_weak_mention(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description=(
                "Java Spring Boot REST API SQL backend services. "
                "Works with adjacent frontend teams using React-heavy frontend components."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 45, company={"name": "Example Co"})

        self.assertNotIn("possible exclusion: react-heavy frontend", job.risk_flags)

    def test_exclusion_penalty_is_applied_when_exclusion_is_dominant(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer - React-heavy frontend",
            description=(
                "React-heavy frontend role. React-heavy frontend work across UI ownership. "
                "React-heavy frontend leadership with some Java APIs."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 45, company={"name": "Example Co"})

        self.assertTrue(any("possible exclusion:" in risk for risk in job.risk_flags))


    def test_existing_required_gap_risk_flag_remains_hard_reject_after_compaction(self) -> None:
        job = self.make_job(
            title="Engineer Senior",
            description="Compacted description with REST APIs and SQL.",
        )
        job.risk_flags = [
            "required experience gaps: NodeJS, Python, Redis/cache, AWS/Azure, Terraform, Splunk, Datadog"
        ]

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Elevance Health"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)


    def test_cpp_as_alternative_backend_language_is_not_a_required_gap(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description=(
                "Proficiency in at least one systems/backend language: Go preferred, "
                "or similar Java, C++, Rust. Designs REST API services and SQL-backed systems."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "NCR Voyix"})

        self.assertNotIn("required C++/Windows experience gap", job.risk_flags)


    def test_application_engineering_with_monitoring_is_not_an_sre_rejection(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description=(
                "Build Java Spring Boot REST API services with SQL. Own application monitoring, "
                "logs, metrics, health endpoints, CI/CD, incident diagnosis, and production support."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Example Co"})

        self.assertNotIn("primary SRE/observability/AIOps platform role", job.risk_flags)
        self.assertNotEqual(job.decision, "Skip")


    def test_incidental_ruby_mention_does_not_trigger_rails_rejection(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description=(
                "Build Java Spring Boot REST API services with SQL. Partner with another team that "
                "maintains a legacy Ruby on Rails administrative tool."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Example Co"})

        self.assertNotIn("primary Ruby on Rails stack mismatch", job.risk_flags)
        self.assertNotEqual(job.decision, "Skip")


    def test_optional_csharp_without_dotnet_is_not_required_dotnet_gap(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description=(
                "Build Java Spring Boot REST API services with SQL. Experience in two "
                "or more programming languages such as C++, Java, C#, or scripting "
                "languages is a plus."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Example Co"})

        self.assertNotIn("required .NET tech stack", job.risk_flags)


    def test_apply_requires_fit_score_greater_than_75(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description="Java Spring Boot REST API SQL backend services",
        )
        job.fit_score = 75

        discover_jobs.assign_decision_from_score(job)

        self.assertEqual(job.decision, "Maybe")

    def test_write_markdown_summary_includes_company_diagnostics(self) -> None:
        jobs = [
            discover_jobs.JobPosting(
                company="Example Co",
                title="Senior Software Engineer",
                url="https://example.com/jobs/1",
                posted_on="2026-07-06",
                posted_age_days=5,
                location="Remote - United States",
                work_model="Remote",
                source="workday",
                fit_score=75,
                decision="Maybe",
                risk_flags=[],
            ),
            discover_jobs.JobPosting(
                company="Example Co",
                title="Principal Solution Architect",
                url="https://example.com/jobs/2",
                posted_on="2026-07-06",
                posted_age_days=5,
                location="Seattle, WA",
                work_model="Onsite",
                source="workday",
                fit_score=20,
                decision="Skip",
                risk_flags=["onsite location is not in the candidate's target geography"],
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "summary.md"
            discover_jobs.write_markdown_summary(
                path,
                jobs,
                [],
                discover_jobs.dt.date(2026, 7, 6),
            )
            content = path.read_text(encoding="utf-8")

        self.assertIn("## Company Diagnostics", content)
        self.assertIn("Example Co", content)
        self.assertIn("onsite location is not in the candidate's target geography", content)


if __name__ == "__main__":
    unittest.main()
