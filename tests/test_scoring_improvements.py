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

    def test_staffing_company_gets_consulting_lane_boost(self) -> None:
        job = self.make_job(
            title="Implementation Consultant",
            description="Java Spring Boot REST API integration consultant with client delivery ownership",
        )
        company = {
            "name": "Insight Global Staffing",
            "notes": "Staffing and consulting target.",
        }

        discover_jobs.score_job(job, BASE_PROFILE, 45, company=company)

        self.assertIn("staffing/consulting lane match", job.fit_reasons)
        self.assertGreaterEqual(job.fit_score, 50)

    def test_consulting_lane_boost_does_not_require_staffing_company(self) -> None:
        job = self.make_job(
            title="Solution Architect",
            description=(
                "Technical advisory role leading client workshops, solution design, "
                "implementation guidance, integrations, REST API, Java, Spring Boot, SQL, "
                "and enterprise application delivery."
            ),
        )
        company = {
            "name": "Enterprise Software Co",
            "notes": "Product and implementation target.",
        }

        discover_jobs.score_job(job, BASE_PROFILE, 45, company=company)

        self.assertIn("consulting/advisory lane match", job.fit_reasons)
        self.assertGreaterEqual(job.fit_score, 50)

    def test_hands_on_forward_deployed_role_gets_lane_match(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Forward Deployed Engineer"],
        }
        job = self.make_job(
            title="Forward Deployed Engineer",
            description=(
                "Design and build production Java Spring Boot services, REST APIs, and "
                "enterprise integrations directly with customers. Own architecture, "
                "implementation, Kubernetes deployment, and production troubleshooting."
            ),
        )

        discover_jobs.score_job(job, profile, 60, company={"name": "Platform Co"})

        self.assertIn("forward deployed engineering lane match", job.fit_reasons)
        self.assertEqual("Apply", job.decision)

    def test_configuration_only_solutions_engineer_is_not_apply(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Solutions Engineer"],
        }
        job = self.make_job(
            title="Solutions Engineer",
            description=(
                "Configure the existing product, deliver pre-sales demonstrations, answer "
                "customer questions, and hand qualified opportunities to account executives."
            ),
        )

        discover_jobs.score_job(job, profile, 60, company={"name": "Platform Co"})

        self.assertNotEqual("Apply", job.decision)
        self.assertIn(
            "customer-facing title lacks substantial software engineering ownership",
            job.risk_flags,
        )

    def test_sales_engineer_disguised_as_solutions_engineer_is_hard_rejected(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Solutions Engineer"],
            "strong_keywords": [
                *BASE_PROFILE["strong_keywords"],
                "kubernetes",
                "docker",
                "distributed systems",
            ],
        }
        job = self.make_job(
            title="Senior Principal Solutions Engineer",
            description=(
                "We are looking for a Sales Engineer to join our global sales team. "
                "Partner with Account Executives to qualify opportunities and drive "
                "technical sales efforts through product demos and proof-of-concept "
                "engagements. Own technical sales wins for a Java Spring Boot SQL "
                "distributed systems platform running on Kubernetes and Docker."
            ),
        )

        discover_jobs.score_job(job, profile, 60, company={"name": "Database Co"})

        self.assertEqual("Skip", job.decision)
        self.assertLessEqual(job.fit_score, 45)
        self.assertIn("primary pre-sales / sales engineering role", job.risk_flags)

    def test_account_solution_architect_is_hard_rejected_as_sales_role(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Solution Architect"],
        }
        job = self.make_job(
            title="Account Solution Architect",
            description=(
                "Support strategic accounts with Java, Spring Boot, REST API, and SQL "
                "architecture throughout the customer lifecycle."
            ),
        )

        discover_jobs.score_job(job, profile, 60)

        self.assertEqual("Skip", job.decision)
        self.assertIn("primary pre-sales / sales engineering role", job.risk_flags)

    def test_technology_sales_solution_architect_is_hard_rejected(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Solution Architect"],
        }
        job = self.make_job(
            title="Global Specialist Solution Architect",
            description=(
                "Join the Technology Sales team to provide deep technical sales support "
                "for go-to-market programs and partner with the Account Executive."
            ),
        )

        discover_jobs.score_job(job, profile, 60)

        self.assertEqual("Skip", job.decision)
        self.assertIn("primary pre-sales / sales engineering role", job.risk_flags)

    def test_primary_mobile_engineer_title_is_hard_rejected(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Staff Software Engineer"],
        }
        job = self.make_job(
            title="Staff Android Software Engineer, Consumer Platform",
            description="Build Java services, REST APIs, SQL data flows, and Android applications.",
        )

        discover_jobs.score_job(job, profile, 60)

        self.assertEqual("Skip", job.decision)
        self.assertIn("primary mobile application engineering role", job.risk_flags)

    def test_remote_uk_role_is_hard_rejected(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description="Java Spring Boot REST API SQL backend services",
        )
        job.location = "Remote UK"
        job.work_model = "Remote"

        discover_jobs.score_job(job, BASE_PROFILE, 60)

        self.assertEqual("Skip", job.decision)
        self.assertIn("remote role appears outside target geography", job.risk_flags)

    def test_co_sell_solutions_engineer_is_hard_rejected(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Solutions Engineer"],
        }
        job = self.make_job(
            title="Staff Solutions Engineer",
            description=(
                "Partner with Sales and Account Management and co-sell the developer "
                "platform. Build API proof-of-concepts and sample integrations, create "
                "compelling product demos and presentations, and bring prior sales "
                "engineering experience supporting enterprise merchants."
            ),
        )

        discover_jobs.score_job(job, profile, 60, company={"name": "Payments Co"})

        self.assertEqual("Skip", job.decision)
        self.assertIn("primary pre-sales / sales engineering role", job.risk_flags)

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

    def test_configuration_only_forward_deployed_title_is_deprioritized(self) -> None:
        profile = {
            **BASE_PROFILE,
            "title_targets": [*BASE_PROFILE["title_targets"], "Forward Deployed Engineer"],
        }
        job = self.make_job(
            title="Forward Deployed Engineer",
            description=(
                "Configure the existing product, conduct demonstrations, coordinate onsite "
                "installation, and escalate software issues to product engineering."
            ),
        )

        discover_jobs.score_job(job, profile, 60, company={"name": "Platform Co"})

        self.assertNotIn(job.decision, {"Apply", "Maybe"})
        self.assertIn(
            "customer-facing title lacks substantial software engineering ownership",
            job.risk_flags,
        )

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

    def test_ai_primary_role_is_not_apply_even_with_senior_engineer_title(self) -> None:
        job = discover_jobs.JobPosting(
            company="Example Co",
            title="Staff Software Engineer Agentic AI and Machine Learning",
            url="https://example.com/jobs/ai",
            location="Remote - United States",
            work_model="Remote",
            posted_age_days=3,
            description="Own agentic AI, RAG, LLM, MCP, Python, model orchestration, and machine learning platforms.",
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60)

        self.assertEqual(job.decision, "Skip")
        self.assertIn("primary AI/ML platform role", job.risk_flags)

    def test_known_foreign_tech_hubs_are_rejected_for_hybrid_roles(self) -> None:
        job = discover_jobs.JobPosting(
            company="Example Co",
            title="Senior Software Engineer",
            url="https://example.com/jobs/foreign",
            location="Raanana",
            work_model="Hybrid",
            posted_age_days=3,
            description="Java Spring Boot SQL backend services",
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60)

        self.assertEqual(job.decision, "Skip")
        self.assertIn("location likely incompatible", job.risk_flags)

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
        self.assertIn("hybrid location outside Atlanta metro", job.risk_flags)

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
        self.assertNotIn("hybrid location outside Atlanta metro", job.risk_flags)

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

    def test_explicit_required_years_gap_prevents_apply_recommendation(self) -> None:
        job = self.make_job(
            title="Engineer Senior",
            description=(
                "Designs, develops, and maintains microservices and REST APIs. "
                "Experience Requirements: Five (5) years of related experience must include: "
                "One (1) year of experience with NodeJS; "
                "Three (3) years of experience with Python; "
                "Three (3) years of experience with Linux; "
                "One (1) year of experience with SQL; "
                "One (1) year of experience with Redis cache or similar; "
                "One (1) year of experience with AWS/Azure; "
                "One (1) year of experience with Kubernetes/Docker; "
                "Three (3) years of experience with Terraform; "
                "Three (3) years of experience with Splunk; "
                "and One (1) year of experience with Datadog. "
                "Must have skills listed above."
            ),
        )
        company = {
            "name": "Elevance Health",
            "title_aliases": ["Engineer Senior"],
        }

        discover_jobs.score_job(job, BASE_PROFILE, 60, company=company)

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertTrue(
            any("required experience gaps" in risk for risk in job.risk_flags),
            job.risk_flags,
        )

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

    def test_explicit_cpp_windows_requirement_prevents_apply_recommendation(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description=(
                "Convenience and Fuel Retail division focused on Payments and Loyalty "
                "software products, payment networks, card brands, and devices. "
                "Basic Qualifications: 7+ years of C++ experience on Windows. "
                "Modern C++ 11+ experience is required."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "NCR Voyix"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertTrue(
            any("required C++/Windows experience gap" in risk for risk in job.risk_flags),
            job.risk_flags,
        )

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

    def test_weather_science_ai_role_is_rejected_as_extreme_stretch(self) -> None:
        job = self.make_job(
            title="Staff Software Engineer, Consumer Weather Science & AI",
            description=(
                "Consumer Weather Science team owns the path from atmospheric model output "
                "to consumer experiences across weather.com and The Weather Channel apps. "
                "Build Python data services, AWS pipelines, production LLM APIs, and "
                "AI-generated consumer forecast products. Translate model output, observations, "
                "and AI-derived insights into APIs and product integrations."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "The Weather Company LLC"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertIn("weather science / AI product role outside target lane", job.risk_flags)

    def test_primary_sre_aiops_observability_role_is_skipped(self) -> None:
        job = discover_jobs.JobPosting(
            company="CVS Health",
            title="Staff Software Engineer - DevOps, SRE, AIOps",
            url="https://example.com/jobs/cvs-sre",
            location="IRL - Galway",
            work_model="Remote/Hybrid",
            posted_age_days=0,
            verification_status="official_live",
            description=(
                "Own the observability platform, define SLOs, SLIs, and error budgets, "
                "and lead incident response and AIOps automation. Required experience includes "
                "OpenTelemetry, Grafana, Loki, Tempo, Prometheus, Argo CD, AWS, Kubernetes, "
                "Terraform, and high-volume data pipelines."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "CVS Health"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertIn("primary SRE/observability/AIOps platform role", job.risk_flags)
        self.assertIn("location likely incompatible", job.risk_flags)

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

    def test_primary_ruby_on_rails_role_is_skipped(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description=(
                "Build scalable APIs and product features. What you will need: extensive Ruby on Rails "
                "experience, relational databases, CI/CD, AWS cloud infrastructure, Docker, Kubernetes, "
                "and monitoring. Our tech stack includes Ruby on Rails, React, AWS, PostgreSQL/RDS, SQS, "
                "and GraphQL. Application question: How many years of experience do you have writing "
                "readable, tested, and efficient code using Ruby on Rails?"
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Omada"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertIn("primary Ruby on Rails stack mismatch", job.risk_flags)

    def test_ruby_rails_production_application_screen_is_skipped(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer, Core Systems & Integrations",
            description=(
                "Build backend systems, integrations, data reliability, third-party APIs, testing, "
                "deployment, and mentoring. Requires 7+ years of backend software engineering using "
                "modern frameworks such as Ruby on Rails and Python. Current technologies include AWS, "
                "Ruby, Rails, Postgres, GraphQL, Docker, and Kubernetes. Application questions ask whether "
                "you are currently or recently working with Ruby/Rails in production, what percentage of "
                "the last 2-3 years was Ruby/Rails, and whether you build production-grade systems with Rails."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Omada"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertIn("primary Ruby on Rails stack mismatch", job.risk_flags)

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

    def test_required_dotnet_stack_prevents_apply_recommendation(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description=(
                "Build secure and scalable web applications and APIs. Qualifications: "
                "Typescript/Javascript, .NET, Golang, Java, Python, SQL, REST APIs, "
                "and cloud architecture."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Aderant"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertIn("required .NET tech stack", job.risk_flags)

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

    def test_required_csharp_stack_prevents_apply_recommendation(self) -> None:
        job = self.make_job(
            title="Technical Consultant",
            description=(
                "Qualifications: knowledge of C# is required. Configure and extend "
                "enterprise applications, troubleshoot integrations, and work with SQL."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Aderant"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertIn("required .NET tech stack", job.risk_flags)

    def test_associate_technical_consultant_is_rejected_as_junior_scope(self) -> None:
        job = self.make_job(
            title="Associate Technical Consultant",
            description=(
                "Implement enterprise applications using Java, Spring Boot, REST APIs, "
                "SQL, integrations, and customer-facing technical delivery."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Aderant"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertIn("junior associate-level role below target scope", job.risk_flags)

    def test_clustered_hard_stack_gaps_prevent_apply_recommendation(self) -> None:
        job = self.make_job(
            title="Senior Software Engineer",
            description=(
                "Build Java backend services, APIs, multi-tenant SaaS, CI/CD, automated "
                "testing, design docs, mentoring, and monolith-to-services migration. "
                "Required stack includes full-stack development with Java, Groovy/Grails, "
                "React, JavaScript, TypeScript, MongoDB NoSQL schema design and query "
                "optimization, Kafka or similar event/message-driven workflows, and "
                "professional cloud deployment and support in AWS or another major cloud. "
                "Application questions ask whether candidates have 4+ years of professional "
                "React experience."
            ),
        )

        discover_jobs.score_job(job, BASE_PROFILE, 60, company={"name": "Cohere Health"})

        self.assertEqual(job.decision, "Skip")
        self.assertLess(job.fit_score, 50)
        self.assertTrue(
            any("hard-stack requirement gaps" in risk for risk in job.risk_flags),
            job.risk_flags,
        )

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
