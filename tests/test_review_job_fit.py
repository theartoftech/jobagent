import json
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.mark_applied_jobs import TrackerJob, write_tracker_jobs_snapshot
from scripts.review_job_fit import (
    classify_review,
    is_excluded_company,
    is_active_carry_forward_review,
    is_skipped_by_job_override,
    load_carry_forward_jobs,
    main,
    merge_current_and_carry_forward_jobs,
)


PROFILE = {
    "confirmed_strengths": ["Java", "Spring Boot", "REST APIs", "SQL", "Oracle CPQ", "quote-to-cash"],
    "do_not_claim": ["React", "AWS", "Go", "ledgering", "settlement", "double-entry accounting"],
    "salary_floor": 150000,
    "hard_reject_if_primary": [
        "React-heavy frontend",
        "Go primary stack",
        "AWS/Terraform/EKS infrastructure",
    ],
    "strategic_companies": ["ExampleCo", "Block", "ReadyCo"],
    "seniority_approvals": {},
}


class ReviewJobFitTests(unittest.TestCase):
    def test_required_named_cloud_years_are_not_met_by_training(self) -> None:
        profile = {**PROFILE, 'do_not_claim': ['AWS', 'Azure', 'GCP'],
                   'candidate_gap_resolutions': {'AWS': 'limited_exposure: training only', 'Azure': 'do_not_claim', 'GCP': 'limited_exposure: training only'}}
        job = {'company': 'Fiserv', 'title': 'Lead Engineer', 'url': 'https://careers.fiserv.com/us/en/job/R-10404240/Lead-Engineer',
               'description': "Experience you'll need to have:\n3+ years of experience in AWS, Azure, or GCP services\nJava and SQL engineering.",
               'decision': 'Apply', 'fit_score': 100}
        review = classify_review(job=job, workflow_profile=profile)
        self.assertFalse(review.should_generate_resume)
        self.assertEqual('SKIP', review.workflow_decision)
        self.assertTrue({'AWS', 'Azure', 'GCP'}.issubset(review.required_gaps))

    def test_annual_salary_range_is_not_truncated_by_mixed_hourly_boilerplate(self) -> None:
        review = classify_review(job={'company': 'ReadyCo', 'title': 'Principal Java Engineer',
                                     'description': 'Build Java Spring Boot REST APIs. Pay Range: $106,605.00 - $260,590.00. This pay range represents the base hourly rate or base annual full-time salary for all positions in the job grade.',
                                     'decision': 'Apply', 'fit_score': 100, 'url': 'https://example.com/annual-range'}, workflow_profile=PROFILE)
        self.assertNotEqual('SKIP', review.workflow_decision)

    def test_cloud_qualifications_cannot_be_cleared_by_unrelated_java(self) -> None:
        description = "Requirements & Qualifications\nBachelor's degree; Master's degree preferred\n" + 'Enterprise architecture experience.\n' * 15 + 'Experience designing enterprise multi-cloud architectures (SAP Cloud, Microsoft Azure, or Google Cloud Platform).\nReporting relationship\nSkills: Java, Kotlin'
        profile = {**PROFILE, 'do_not_claim': [*PROFILE['do_not_claim'], 'Azure', 'GCP'],
                   'required_capability_evidence': [{'label': 'Enterprise multi-cloud architecture', 'terms': ['multi-cloud'], 'title_pattern': r'cloud.*architect', 'evidence_status': 'unconfirmed'}]}
        review = classify_review(job={'company': 'Coke', 'title': 'Cloud Architect', 'description': description,
                                     'decision': 'Apply', 'fit_score': 100, 'url': 'https://example.com/R-144531'}, workflow_profile=profile)
        self.assertEqual('SKIP', review.workflow_decision)
        self.assertFalse(review.should_generate_resume)
        self.assertTrue({'Azure', 'GCP', 'Enterprise multi-cloud architecture'}.issubset(review.required_gaps))

    def test_missing_middle_qualifications_require_official_refresh(self) -> None:
        review = classify_review(job={'company': 'ReadyCo', 'title': 'Principal Java Engineer',
                                     'description': 'Build Java Spring Boot REST APIs [middle omitted] SQL.',
                                     'decision': 'Apply', 'fit_score': 100, 'url': 'https://example.com/incomplete'}, workflow_profile=PROFILE)
        self.assertFalse(review.should_generate_resume)
        self.assertIn('incomplete qualification evidence; refresh official posting', review.hard_blockers)
    def test_pending_availability_remains_a_verification_item(self) -> None:
        pending = {
            "workflow_decision": "SKIP", "should_generate_resume": False, "already_applied": False,
            "hard_blockers": ["official posting availability not verified: verification_required"],
        }
        self.assertTrue(is_active_carry_forward_review(pending))
        self.assertFalse(is_active_carry_forward_review({**pending, "already_applied": True}))

    def test_nonlive_posting_is_blocked_even_with_high_score(self) -> None:
        for status in ("official_unavailable", "verification_failed", "verification_required"):
            with self.subTest(status=status):
                review = classify_review(job={
                    "company": "ReadyCo", "title": "Principal Java Engineer",
                    "description": "Own Java Spring Boot REST APIs and SQL architecture.",
                    "url": "https://example.com/jobs/closed", "decision": "Apply",
                    "fit_score": 100, "verification_status": status,
                }, workflow_profile=PROFILE)
                self.assertEqual("SKIP", review.workflow_decision)
                self.assertFalse(review.should_generate_resume)
                self.assertTrue(any("availability" in blocker for blocker in review.hard_blockers))

    def test_expired_cached_posting_cannot_authorize_documents(self) -> None:
        review = classify_review(job={
            "company": "ReadyCo", "title": "Principal Java Engineer",
            "description": "Anticipated End Date: 2020-09-29 Own Java Spring Boot REST APIs and SQL architecture.",
            "url": "https://example.com/jobs/expired", "decision": "Apply", "fit_score": 100,
        }, workflow_profile=PROFILE)
        self.assertEqual("SKIP", review.workflow_decision)
        self.assertFalse(review.should_generate_resume)

    def test_primary_guidewire_role_cannot_reach_resume_queue(self) -> None:
        job = {
            "company": "Travelers", "title": "Senior Software Engineer, Guidewire BillingCenter",
            "description": "Configure Guidewire BillingCenter using Java, Spring Boot, REST APIs and SQL. What Will Our Ideal Candidate Have? 5+ years of Guidewire BillingCenter configuration and integration experience.",
            "location": "Remote US", "work_model": "Remote", "decision": "Apply", "fit_score": 100,
            "risk_flags": [], "url": "https://example.com/jobs/R-53073",
        }
        review = classify_review(job=job, workflow_profile={**PROFILE, "unsupported_primary_platforms": ["Guidewire"]})
        self.assertEqual("SKIP", review.workflow_decision)
        self.assertFalse(review.should_generate_resume)
        self.assertIn("unsupported primary platform: Guidewire", review.hard_blockers)
        self.assertIn("Guidewire", review.required_gaps)

    def test_java_or_gosu_is_not_an_alternative_to_guidewire_platform_expertise(self) -> None:
        job = {
            "company": "ExampleCo", "title": "Guidewire BillingCenter Engineer",
            "description": "Required: experience with Java or Gosu for Guidewire BillingCenter development.",
            "location": "Remote US", "work_model": "Remote", "decision": "Apply", "fit_score": 100,
            "risk_flags": [], "url": "https://example.com/jobs/language-alternative",
        }
        review = classify_review(job=job, workflow_profile={
            **PROFILE, "unsupported_primary_platforms": ["Guidewire"],
            "do_not_claim": [*PROFILE["do_not_claim"], "Guidewire"],
        })
        self.assertEqual("SKIP", review.workflow_decision)
        self.assertIn("Guidewire", review.required_gaps)

    def test_optional_guidewire_mention_does_not_reject_java_architecture(self) -> None:
        job = {
            "company": "ExampleCo", "title": "Principal Java Engineer",
            "description": "Own Java Spring Boot REST APIs and SQL architecture. Familiarity with Guidewire for an optional integration is a plus.",
            "location": "Remote US", "work_model": "Remote", "decision": "Apply", "fit_score": 100,
            "risk_flags": [], "url": "https://example.com/jobs/optional-platform",
        }
        review = classify_review(job=job, workflow_profile={**PROFILE, "unsupported_primary_platforms": ["Guidewire"]})
        self.assertEqual("STRONG APPLY", review.workflow_decision)
        self.assertNotIn("unsupported primary platform: Guidewire", review.hard_blockers)

    def test_primary_platform_delivery_in_body_is_blocked_without_title_keyword(self) -> None:
        job = {
            "company": "ExampleCo", "title": "Principal Engineer",
            "description": "Configure Guidewire BillingCenter integrations using Java and SQL.",
            "location": "Remote US", "work_model": "Remote", "decision": "Apply", "fit_score": 100,
            "risk_flags": [], "url": "https://example.com/jobs/platform-delivery",
        }
        review = classify_review(job=job, workflow_profile={**PROFILE, "unsupported_primary_platforms": ["Guidewire"]})
        self.assertEqual("SKIP", review.workflow_decision)
        self.assertFalse(review.should_generate_resume)

    def test_published_compensation_maximum_below_floor_is_a_hard_blocker(self) -> None:
        job = {
            "company": "ExampleCo",
            "title": "Principal Java Engineer",
            "description": (
                "Build Java services. The estimated annual salary range is "
                "$87,500.00-$139,250.00."
            ),
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 95,
            "risk_flags": [],
            "url": "https://example.com/jobs/below-floor",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("SKIP", review.workflow_decision)
        self.assertFalse(review.should_generate_resume)
        self.assertIn(
            "published compensation maximum $139,250 does not exceed $150,000 floor",
            review.hard_blockers,
        )

    def test_published_compensation_must_strictly_exceed_floor(self) -> None:
        job = {
            "company": "ExampleCo",
            "title": "Principal Java Engineer",
            "description": "Build Java services. Base salary range: $140k-$150k annually.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 95,
            "risk_flags": [],
            "url": "https://example.com/jobs/at-floor",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("SKIP", review.workflow_decision)
        self.assertIn(
            "published compensation maximum $150,000 does not exceed $150,000 floor",
            review.hard_blockers,
        )

    def test_published_compensation_above_floor_remains_eligible(self) -> None:
        job = {
            "company": "ExampleCo",
            "title": "Principal Java Engineer",
            "description": "Build Java services. Annual compensation range: $145,000-$150,001.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 95,
            "risk_flags": [],
            "url": "https://example.com/jobs/above-floor",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("STRONG APPLY", review.workflow_decision)
        self.assertFalse(any("compensation maximum" in item for item in review.hard_blockers))

    def test_unpublished_or_hourly_compensation_does_not_trigger_annual_floor(self) -> None:
        descriptions = [
            "Build Java services. Compensation is competitive.",
            "Build Java services. Contract pay range is $70-$90 per hour.",
        ]

        for index, description in enumerate(descriptions):
            with self.subTest(description=description):
                job = {
                    "company": "ExampleCo",
                    "title": "Principal Java Engineer",
                    "description": description,
                    "location": "Remote US",
                    "work_model": "Remote",
                    "decision": "Apply",
                    "fit_score": 95,
                    "risk_flags": [],
                    "url": f"https://example.com/jobs/unknown-{index}",
                }

                review = classify_review(job=job, workflow_profile=PROFILE)

                self.assertEqual("STRONG APPLY", review.workflow_decision)
                self.assertFalse(
                    any("compensation maximum" in item for item in review.hard_blockers)
                )

    def test_invalid_salary_floor_fails_explicitly(self) -> None:
        job = {
            "company": "ExampleCo",
            "title": "Principal Java Engineer",
            "description": "Build Java services. Annual salary range: $160,000-$180,000.",
            "decision": "Apply",
            "fit_score": 95,
            "url": "https://example.com/jobs/invalid-floor",
        }
        profile = {**PROFILE, "salary_floor": "not-a-number"}

        with self.assertRaisesRegex(ValueError, "salary_floor must be a positive number"):
            classify_review(job=job, workflow_profile=profile)

    def test_expired_company_override_does_not_exclude_role(self) -> None:
        job = {
            "company": "Block",
            "title": "Staff Software Engineer",
            "url": "https://block.xyz/careers/jobs/123",
            "job_id": "123",
        }
        overrides = [
            {
                "company": "Block",
                "decision": "Skip",
                "expires_on": "2026-10-28",
            }
        ]

        self.assertTrue(
            is_skipped_by_job_override(job, overrides, as_of=dt.date(2026, 10, 28))
        )
        self.assertFalse(
            is_skipped_by_job_override(job, overrides, as_of=dt.date(2026, 10, 29))
        )

    def test_job_override_title_marker_excludes_role_from_strict_review(self) -> None:
        job = {
            "company": "Block",
            "title": "Staff Android Software Engineer, Consumer Platform",
            "url": "https://example.com/jobs/1",
            "job_id": "1",
        }
        overrides = [
            {
                "title_contains": "Android Software Engineer",
                "decision": "Skip",
            }
        ]

        self.assertTrue(is_skipped_by_job_override(job, overrides))

    def test_excluded_employer_is_filtered_by_exact_company_name(self) -> None:
        profile = {"excluded_companies": ["Oracle"]}

        self.assertTrue(is_excluded_company({"company": "Oracle"}, profile))
        self.assertFalse(is_excluded_company({"company": "Oracle Health partner"}, profile))

    def test_merge_dedupes_same_requisition_across_official_location_urls(self) -> None:
        current = [
            {
                "company": "The Home Depot",
                "title": "Senior Principal Software Engineer",
                "job_id": "Req186005",
                "url": "https://example.com/job/remote/Req186005",
            },
            {
                "company": "The Home Depot",
                "title": "Senior Principal Software Engineer",
                "job_id": "Req186005",
                "url": "https://example.com/job/oregon/Req186005",
            },
            {
                "company": "The Home Depot",
                "title": "Senior Principal Software Engineer",
                "job_id": "Req999999",
                "url": "https://example.com/job/remote/Req999999",
            },
        ]

        merged = merge_current_and_carry_forward_jobs(current, [])

        self.assertEqual(2, len(merged))
        self.assertEqual(["Req186005", "Req999999"], [job["job_id"] for job in merged])

    def test_load_carry_forward_jobs_keeps_only_active_strict_reviews(self) -> None:
        source_jobs = [
            {
                "company": "ReadyCo",
                "title": "Senior Java Engineer",
                "description": "Build Java services.",
                "decision": "Apply",
                "fit_score": 90,
                "url": "https://example.com/ready",
            },
            {
                "company": "QuestionCo",
                "title": "Platform Engineer",
                "description": "Build Java and Go services.",
                "decision": "Maybe",
                "fit_score": 82,
                "url": "https://example.com/questions",
            },
            {
                "company": "AppliedCo",
                "title": "Staff Engineer",
                "description": "Build Java services.",
                "decision": "Apply",
                "fit_score": 88,
                "url": "https://example.com/applied",
            },
            {
                "company": "SkipCo",
                "title": "Frontend Engineer",
                "description": "Build React applications.",
                "decision": "Maybe",
                "fit_score": 60,
                "url": "https://example.com/skip",
            },
        ]
        reviews = [
            {
                "company": "ReadyCo",
                "url": "https://example.com/ready",
                "workflow_decision": "STRONG APPLY",
                "should_generate_resume": True,
                "gap_questions_needed": False,
                "already_applied": False,
            },
            {
                "company": "QuestionCo",
                "url": "https://example.com/questions",
                "workflow_decision": "APPLY",
                "should_generate_resume": False,
                "gap_questions_needed": True,
                "already_applied": False,
            },
            {
                "company": "AppliedCo",
                "url": "https://example.com/applied",
                "workflow_decision": "APPLY",
                "should_generate_resume": False,
                "gap_questions_needed": False,
                "already_applied": True,
            },
            {
                "company": "SkipCo",
                "url": "https://example.com/skip",
                "workflow_decision": "SKIP",
                "should_generate_resume": False,
                "gap_questions_needed": False,
                "already_applied": False,
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            (out_dir / "2026-07-22-workflow-review.json").write_text(
                json.dumps(
                    {
                        "generated_on": "2026-07-22",
                        "source_jobs": source_jobs,
                        "reviews": reviews,
                    }
                ),
                encoding="utf-8",
            )

            carried = load_carry_forward_jobs(out_dir=out_dir, run_date="2026-07-23")

        self.assertEqual(
            ["https://example.com/ready", "https://example.com/questions"],
            [str(job["url"]) for job in carried],
        )
        self.assertTrue(all(job["verification_status"] == "verification_required" for job in carried))
        self.assertTrue(all(not classify_review(job=job, workflow_profile=PROFILE).should_generate_resume for job in carried))

    def test_load_carry_forward_matches_review_url_when_source_has_job_id(self) -> None:
        source_jobs = [
            {
                "company": "ReadyCo",
                "title": "Principal Java Engineer",
                "job_id": "REQ-123",
                "description": "Build Java services.",
                "decision": "Apply",
                "fit_score": 90,
                "url": "https://example.com/jobs/REQ-123",
            }
        ]
        reviews = [
            {
                "company": "ReadyCo",
                "title": "Principal Java Engineer",
                "url": "https://example.com/jobs/REQ-123",
                "workflow_decision": "STRONG APPLY",
                "should_generate_resume": True,
                "gap_questions_needed": False,
                "already_applied": False,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            (out_dir / "2026-08-05-workflow-review.json").write_text(
                json.dumps({"reviews": reviews, "source_jobs": source_jobs}),
                encoding="utf-8",
            )

            carried = load_carry_forward_jobs(out_dir=out_dir, run_date="2026-08-10")

        self.assertEqual(1, len(carried))
        self.assertEqual("REQ-123", carried[0]["job_id"])

    def test_apply_java_backend_without_questions(self) -> None:
        job = {
            "company": "ExampleCo",
            "title": "Senior Software Engineer, Java Platform",
            "job_id": "REQ-12345",
            "description": "Build Java Spring Boot REST APIs with SQL for enterprise SaaS workflows.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 86,
            "risk_flags": [],
            "url": "https://example.com/job",
        }
        review = classify_review(job=job, workflow_profile=PROFILE)
        self.assertEqual(review.job_id, "REQ-12345")
        self.assertEqual(review.workflow_decision, "APPLY")
        self.assertTrue(review.should_generate_resume)
        self.assertEqual(review.recommended_resume_base, "general")
        self.assertEqual(review.overqualified_risk, "low")
        self.assertEqual(review.resume_positioning, "candidate_evidence")

    def test_configured_associate_technical_consultant_title_is_rejected(self) -> None:
        profile = {
            **PROFILE,
            "excluded_role_title_markers": ["associate technical consultant"],
        }
        job = {
            "company": "Aderant",
            "title": "Associate Technical Consultant",
            "description": "Implement Java and SQL enterprise integrations for customers.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "risk_flags": [],
            "url": "https://example.com/associate-consultant",
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertEqual("SKIP", review.workflow_decision)
        self.assertFalse(review.should_generate_resume)
        self.assertIn(
            "excluded role type: associate technical consultant",
            review.hard_blockers,
        )

    def test_confirmed_language_satisfies_disjunctive_required_language_list(self) -> None:
        job = {
            "company": "Fresh Consulting",
            "title": "Staff Software Engineer (Distributed Systems and Orchestration)",
            "description": (
                "Requirements: 15 years of software development experience with strong "
                "proficiency in a systems language such as Go, Rust, C++, or Java. "
                "Lead distributed systems, Kubernetes orchestration, CI/CD, and technical leadership."
            ),
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 88,
            "risk_flags": [],
            "url": "https://example.com/disjunctive-language-list",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertNotIn("Go", review.required_gaps)
        self.assertNotIn("Go primary stack", review.hard_blockers)
        self.assertEqual("STRONG APPLY", review.workflow_decision)
        self.assertTrue(review.should_generate_resume)

    def test_confirmed_language_does_not_satisfy_conjunctive_required_language_list(self) -> None:
        job = {
            "company": "PlatformCo",
            "title": "Staff Software Engineer",
            "description": (
                "Requirements: professional Java and Go experience. "
                "Lead distributed systems, Kubernetes orchestration, CI/CD, and technical leadership."
            ),
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 88,
            "risk_flags": [],
            "url": "https://example.com/conjunctive-language-list",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertIn("Go", review.required_gaps)
        self.assertIn("Go primary stack", review.hard_blockers)
        self.assertEqual("SKIP", review.workflow_decision)
        self.assertFalse(review.should_generate_resume)

    def test_explicit_nonremote_location_outside_atlanta_is_rejected(self) -> None:
        profile = {**PROFILE, "hybrid_allowed_metro_tokens": ["Atlanta", "Douglasville"]}
        job = {
            "company": "Mastercard",
            "title": "Senior Software Engineer - Java",
            "description": "Build Java APIs and distributed systems.",
            "location": "Oslo, Norway",
            "work_model": "Unknown",
            "decision": "Apply",
            "fit_score": 88,
            "risk_flags": ["work model unclear"],
            "url": "https://example.com/oslo-java",
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertIn("physical location outside candidate commute area", review.hard_blockers)
        self.assertEqual("SKIP", review.workflow_decision)
        self.assertFalse(review.should_generate_resume)

    def test_generic_us_location_with_unknown_work_model_is_not_rejected(self) -> None:
        profile = {**PROFILE, "hybrid_allowed_metro_tokens": ["Atlanta", "Douglasville"]}
        job = {
            "company": "PlatformCo",
            "title": "Principal Software Engineer",
            "description": "Build Java APIs and distributed systems.",
            "location": "United States",
            "work_model": "Unknown",
            "decision": "Apply",
            "fit_score": 88,
            "risk_flags": ["work model unclear"],
            "url": "https://example.com/us-location",
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertNotIn("physical location outside candidate commute area", review.hard_blockers)


    def test_required_gap_blocks_resume_workflow(self) -> None:
        job = {
            "company": "FinanceCo",
            "title": "Senior Software Engineer, Ledgering",
            "description": "Required experience with ledgering, settlement, AWS, and double-entry accounting.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 82,
            "risk_flags": [],
            "url": "https://example.com/ledger",
        }
        review = classify_review(job=job, workflow_profile=PROFILE)
        self.assertIn(review.workflow_decision, {"EXTREME STRETCH", "SKIP"})
        self.assertFalse(review.should_generate_resume)
        self.assertTrue(review.required_gaps)

    def test_moderate_stretch_does_not_generate_resume(self) -> None:
        job = {
            "company": "Block",
            "title": "Senior Software Engineer, Ledgering",
            "description": "Build Java services. Preferred exposure to ledgering and settlement systems.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Maybe",
            "fit_score": 68,
            "risk_flags": [],
            "url": "https://example.com/block",
        }
        review = classify_review(job=job, workflow_profile=PROFILE)
        self.assertEqual(review.workflow_decision, "MODERATE STRETCH")
        self.assertFalse(review.should_generate_resume)

    def test_strong_moderate_stretch_with_clear_java_core_can_generate_resume(self) -> None:
        job = {
            "company": "ExampleCo",
            "title": "Lead Software Engineer",
            "description": "Lead hands-on Java Spring Boot API modernization and production ownership.",
            "location": "Atlanta, GA",
            "work_model": "Hybrid",
            "decision": "Maybe",
            "fit_score": 75,
            "risk_flags": [],
            "url": "https://example.com/strong-moderate",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("MODERATE STRETCH", review.workflow_decision)
        self.assertEqual("low", review.overqualified_risk)
        self.assertTrue(review.should_generate_resume)

    def test_skill_matching_uses_whole_terms_and_routes_solutions_engineer_resume(self) -> None:
        job = {
            "company": "Block",
            "title": "Senior Solutions Engineer",
            "description": (
                "Build customer-facing API integrations. The old limitation is gone, "
                "and applicable privacy laws still govern the work."
            ),
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "risk_flags": [],
            "url": "https://example.com/block-solutions",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual(review.workflow_decision, "STRONG APPLY")
        self.assertEqual([], review.preferred_gaps)
        self.assertEqual([], review.questions_for_candidate)
        self.assertTrue(review.should_generate_resume)
        self.assertEqual("general", review.recommended_resume_base)

    def test_forward_deployed_engineer_routes_to_consulting_architecture_resume(self) -> None:
        job = {
            "company": "ExampleCo",
            "title": "Forward Deployed Engineer",
            "description": (
                "Build Java Spring Boot production software with customers. Own solution "
                "architecture, enterprise integrations, deployment, and troubleshooting."
            ),
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "risk_flags": [],
            "url": "https://example.com/forward-deployed",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("general", review.recommended_resume_base)
        self.assertEqual("STRONG APPLY", review.workflow_decision)

    def test_skill_matching_still_detects_standalone_go_and_aws(self) -> None:
        job = {
            "company": "CloudCo",
            "title": "Senior Solutions Engineer",
            "description": "Go and AWS are nice-to-have technologies for customer integrations.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "risk_flags": [],
            "url": "https://example.com/cloud-solutions",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual(["AWS", "Go"], review.preferred_gaps)
        self.assertFalse(review.should_generate_resume)

    def test_answered_preferred_gaps_clear_questions_without_claiming_skills(self) -> None:
        job = {
            "company": "Block",
            "title": "Senior Software Engineer, Product Platform",
            "description": "Java backend role. Go and Kafka are nice-to-have technologies.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "risk_flags": [],
            "url": "https://example.com/block-product",
        }
        profile = {
            **PROFILE,
            "do_not_claim": [*PROFILE["do_not_claim"], "Kafka"],
            "gap_resolutions": {
                "https://example.com/block-product": {
                    "Go": "do_not_claim",
                    "Kafka": "do_not_claim",
                }
            },
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertEqual("STRONG APPLY", review.workflow_decision)
        self.assertEqual(["Go", "Kafka"], review.preferred_gaps)
        self.assertEqual({"Go": "do_not_claim", "Kafka": "do_not_claim"}, review.resolved_gaps)
        self.assertEqual([], review.questions_for_candidate)
        self.assertTrue(review.should_generate_resume)

    def test_candidate_wide_answer_suppresses_duplicate_questions(self) -> None:
        profile = {
            **PROFILE,
            "candidate_gap_resolutions": {"AWS": "do_not_claim"},
        }
        jobs = [
            {
                "company": "CloudCo",
                "title": f"Staff Java Engineer {index}",
                "description": "Build Java services. AWS experience is preferred.",
                "location": "Remote US",
                "work_model": "Remote",
                "decision": "Apply",
                "fit_score": 90,
                "risk_flags": [],
                "url": f"https://example.com/cloud-{index}",
            }
            for index in (1, 2)
        ]

        reviews = [classify_review(job=job, workflow_profile=profile) for job in jobs]

        for review in reviews:
            self.assertEqual({"AWS": "do_not_claim"}, review.resolved_gaps)
            self.assertEqual([], review.questions_for_candidate)
            self.assertTrue(review.should_generate_resume)

    def test_candidate_gap_resolution_applies_across_postings(self) -> None:
        profile = {
            **PROFILE,
            "candidate_gap_resolutions": {
                "AWS": "limited_exposure: training only; no professional production experience",
            },
        }
        job = {
            "company": "CloudCo",
            "title": "Principal Java Engineer",
            "description": "Build Java services. AWS experience is preferred.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "risk_flags": [],
            "url": "https://example.com/cloud-principal",
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertEqual(
            {"AWS": "limited_exposure: training only; no professional production experience"},
            review.resolved_gaps,
        )
        self.assertEqual([], review.questions_for_candidate)

    def test_candidate_limited_exposure_does_not_clear_required_gap(self) -> None:
        profile = {
            **PROFILE,
            "candidate_gap_resolutions": {
                "AWS": "limited_exposure: training only; no professional production experience",
            },
        }
        job = {
            "company": "CloudCo",
            "title": "Principal Java Engineer",
            "description": "Required qualifications include professional AWS experience.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "risk_flags": [],
            "url": "https://example.com/cloud-required",
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertEqual(["AWS"], review.required_gaps)
        self.assertEqual([], review.questions_for_candidate)
        self.assertIn(review.workflow_decision, {"SKIP", "EXTREME STRETCH"})
        self.assertFalse(review.should_generate_resume)

    def test_posting_gap_resolution_overrides_candidate_wide_answer(self) -> None:
        job_url = "https://example.com/cloud-specific"
        profile = {
            **PROFILE,
            "candidate_gap_resolutions": {"AWS": "do_not_claim"},
            "gap_resolutions": {
                job_url: {
                    "AWS": "posting_false_positive: AWS is listed only as an example",
                }
            },
        }
        job = {
            "company": "CloudCo",
            "title": "Principal Java Engineer",
            "description": "Build Java services. AWS experience is preferred.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "risk_flags": [],
            "url": job_url,
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertEqual(
            {"AWS": "posting_false_positive: AWS is listed only as an example"},
            review.resolved_gaps,
        )

    def test_candidate_wide_posting_false_positive_is_rejected(self) -> None:
        profile = {
            **PROFILE,
            "candidate_gap_resolutions": {
                "AWS": "posting_false_positive: AWS is listed only as an example",
            },
        }
        job = {
            "company": "CloudCo",
            "title": "Principal Java Engineer",
            "description": "Build Java services. AWS experience is preferred.",
            "decision": "Apply",
            "fit_score": 90,
            "url": "https://example.com/cloud",
        }

        with self.assertRaisesRegex(ValueError, "cannot use posting_false_positive"):
            classify_review(job=job, workflow_profile=profile)

    def test_gap_resolution_is_scoped_to_exact_posting_url(self) -> None:
        job = {
            "company": "Block",
            "title": "Senior Software Engineer, Product Platform",
            "description": "Java backend role. Go is a nice-to-have technology.",
            "decision": "Apply",
            "fit_score": 90,
            "url": "https://example.com/a-different-posting",
        }
        profile = {
            **PROFILE,
            "gap_resolutions": {
                "https://example.com/block-product": {"Go": "do_not_claim"},
            },
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertEqual({}, review.resolved_gaps)
        self.assertNotEqual([], review.questions_for_candidate)
        self.assertFalse(review.should_generate_resume)

    def test_invalid_gap_resolution_fails_explicitly(self) -> None:
        job = {
            "company": "Block",
            "title": "Senior Software Engineer",
            "description": "Go is preferred.",
            "decision": "Apply",
            "fit_score": 90,
            "url": "https://example.com/block",
        }
        profile = {
            **PROFILE,
            "gap_resolutions": {
                "https://example.com/block": {"Go": "pretend_experience"},
            },
        }

        with self.assertRaisesRegex(ValueError, "Unsupported gap resolution"):
            classify_review(job=job, workflow_profile=profile)

    def test_already_applied_job_is_excluded_from_resume_workflow(self) -> None:
        job = {
            "company": "ReadyCo",
            "title": "Senior Java Engineer",
            "description": "Build Java Spring Boot REST APIs with SQL.",
            "decision": "Apply",
            "fit_score": 90,
            "url": "https://example.com/jobs/123456",
        }
        tracker_job = TrackerJob(
            company="ReadyCo",
            title="Senior Java Engineer",
            status="applied",
            job_url="https://example.com/jobs/123456",
            applied_date="2026-07-20",
        )

        review = classify_review(job=job, workflow_profile=PROFILE, tracker_jobs=[tracker_job])

        self.assertTrue(review.already_applied)
        self.assertEqual("2026-07-20", review.applied_date)
        self.assertFalse(review.should_generate_resume)
        self.assertEqual([], review.questions_for_candidate)
        self.assertEqual("Already applied — do not generate another resume.", review.final_action)

    def test_short_skill_names_do_not_match_inside_larger_words(self) -> None:
        profile = {
            **PROFILE,
            "hard_reject_if_primary": [*PROFILE["hard_reject_if_primary"], "Salesforce platform primary stack"],
        }
        job = {
            "company": "ExampleCo",
            "title": "Senior Solutions Engineer",
            "description": "Build JavaScript workflows after an older limitation is gone under privacy laws.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "risk_flags": [],
            "url": "https://example.com/word-boundaries",
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertNotIn("Java", review.direct_matches)
        self.assertEqual([], review.preferred_gaps)
        self.assertEqual([], review.hard_blockers)

    def test_generic_data_flow_does_not_trigger_salesforce_platform_rejection(self) -> None:
        profile = {
            **PROFILE,
            "do_not_claim": [*PROFILE["do_not_claim"], "Azure", "GCP"],
            "hard_reject_if_primary": [*PROFILE["hard_reject_if_primary"], "Salesforce platform primary stack"],
        }
        job = {
            "company": "C3 AI",
            "title": "Forward Deployed Team Lead",
            "description": (
                "Own data flow, integration topology, REST APIs, and ERP or CRM integrations. "
                "Write production Java services and lead customer deployments into customer "
                "cloud environments (AWS, Azure, GCP). Meet enterprise security requirements. "
                "Preferred Qualifications: Experience with AI platforms. Familiarity with AWS, Azure, or GCP."
            ),
            "location": "Atlanta, Georgia",
            "work_model": "Hybrid",
            "decision": "Maybe",
            "fit_score": 80,
            "risk_flags": [],
            "url": "https://example.com/forward-deployed",
        }

        review = classify_review(job=job, workflow_profile=profile)

        self.assertNotIn("Salesforce platform primary stack", review.hard_blockers)
        self.assertEqual([], review.required_gaps)

    def test_main_reviews_only_apply_and_maybe_and_writes_all_outputs(self) -> None:
        jobs = {
            "jobs": [
                {
                    "company": "ReadyCo",
                    "title": "Senior Java Engineer",
                    "description": "Build Java Spring Boot REST APIs with SQL.",
                    "decision": "Apply",
                    "fit_score": 86,
                    "url": "https://example.com/ready",
                },
                {
                    "company": "ReviewCo",
                    "title": "Java Engineer",
                    "description": "Build Java services with preferred React exposure.",
                    "decision": "Maybe",
                    "fit_score": 68,
                    "url": "https://example.com/review",
                },
                {
                    "company": "IgnoredCo",
                    "title": "Principal Java Engineer",
                    "description": "Build Java Spring Boot services.",
                    "decision": "Skip",
                    "fit_score": 99,
                    "url": "https://example.com/ignored",
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            discovered_path = root / "2026-07-22-discovered-jobs.json"
            profile_path = root / "candidate_workflow_profile.yml"
            applied_jobs_path = root / "2026-07-22-applied-jobs.json"
            out_dir = root / "outputs"
            out_dir.mkdir()
            # A current Skip must suppress the previous positive record.
            stale_source = {**jobs["jobs"][2], "decision": "Apply"}
            (out_dir / "2026-07-21-workflow-review.json").write_text(json.dumps({
                "source_jobs": [stale_source], "reviews": [{
                    **stale_source, "workflow_decision": "STRONG APPLY", "should_generate_resume": True,
                    "already_applied": False,
                }],
            }), encoding="utf-8")
            discovered_path.write_text(json.dumps(jobs), encoding="utf-8")
            profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
            write_tracker_jobs_snapshot(applied_jobs_path, jobs=[], ssh_host="test-host")
            argv = [
                "review_job_fit.py",
                "--discovered-jobs",
                str(discovered_path),
                "--workflow-profile",
                str(profile_path),
                "--applied-jobs",
                str(applied_jobs_path),
                "--out",
                str(out_dir),
                "--date",
                "2026-07-22",
            ]

            with mock.patch.object(sys, "argv", argv):
                main()

            workflow_json = (out_dir / "2026-07-22-workflow-review.json").read_text(encoding="utf-8")
            workflow_payload = json.loads(workflow_json)
            packets = (out_dir / "2026-07-22-human-review-packets.md").read_text(encoding="utf-8")
            resume_ready = (out_dir / "2026-07-22-resume-ready-jobs.md").read_text(encoding="utf-8")
            workflow_html = (out_dir / "2026-07-22-workflow-review.html").read_text(encoding="utf-8")

        self.assertIn("ReadyCo", workflow_json)
        self.assertEqual(2, workflow_payload["current_source_job_count"])
        self.assertEqual(0, workflow_payload["carry_forward_source_job_count"])
        self.assertIn("ReviewCo", workflow_json)
        self.assertNotIn("IgnoredCo", workflow_json)
        self.assertIn("ReviewCo", packets)
        self.assertIn("MODERATE STRETCH", packets)
        self.assertIn("ReadyCo", resume_ready)
        self.assertNotIn("ReviewCo", resume_ready)
        self.assertIn("ReadyCo", workflow_html)
        self.assertIn("ReviewCo", workflow_html)


if __name__ == "__main__":
    unittest.main()
