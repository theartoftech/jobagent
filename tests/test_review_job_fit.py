import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.mark_applied_jobs import TrackerJob, write_tracker_jobs_snapshot
from scripts.review_job_fit import (
    classify_review,
    is_excluded_company,
    load_carry_forward_jobs,
    main,
    merge_current_and_carry_forward_jobs,
)


PROFILE = {
    "confirmed_strengths": ["Java", "Spring Boot", "REST APIs", "SQL", "Oracle CPQ", "quote-to-cash"],
    "do_not_claim": ["React", "AWS", "Go", "ledgering", "settlement", "double-entry accounting"],
    "hard_reject_if_primary": [
        "React-heavy frontend",
        "Go primary stack",
        "AWS/Terraform/EKS infrastructure",
    ],
    "strategic_companies": ["ExampleCo", "Block", "ReadyCo"],
    "seniority_approvals": {},
}


class ReviewJobFitTests(unittest.TestCase):
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
            "description": "Build Java Spring Boot REST APIs with SQL for enterprise SaaS workflows.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 86,
            "risk_flags": [],
            "url": "https://example.com/job",
        }
        review = classify_review(job=job, workflow_profile=PROFILE)
        self.assertEqual(review.workflow_decision, "APPLY")
        self.assertTrue(review.should_generate_resume)
        self.assertEqual(review.recommended_resume_base, "software_engineering")
        self.assertEqual(review.overqualified_risk, "medium")
        self.assertEqual(review.resume_positioning, "senior_lead_execution_forward")

    def test_principal_role_has_low_overqualified_risk_and_architect_positioning(self) -> None:
        job = {
            "company": "PlatformCo",
            "title": "Principal Software Engineer",
            "description": "Own Java platform architecture, modernization, design reviews, and cross-team technical direction.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 90,
            "url": "https://example.com/principal",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("low", review.overqualified_risk)
        self.assertEqual("principal_architect_forward", review.resume_positioning)
        self.assertTrue(review.should_generate_resume)

    def test_non_strategic_feature_delivery_senior_role_is_high_risk_and_not_resume_ready(self) -> None:
        job = {
            "company": "OrdinaryCo",
            "title": "Senior Software Engineer",
            "description": "Deliver assigned product features in two-week sprints using Java and SQL.",
            "location": "Remote US",
            "work_model": "Remote",
            "decision": "Apply",
            "fit_score": 86,
            "url": "https://example.com/senior-feature-delivery",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("high", review.overqualified_risk)
        self.assertEqual("senior_lead_execution_forward", review.resume_positioning)
        self.assertFalse(review.should_generate_resume)
        self.assertIn("explicitly approves", review.final_action)
        self.assertIn("Use reduced-scope interview positioning", review.interview_positioning_note)

    def test_senior_role_with_broad_technical_ownership_is_medium_risk_and_resume_ready(self) -> None:
        job = {
            "company": "ModernizationCo",
            "title": "Lead Software Engineer",
            "description": (
                "Lead Java platform modernization, production ownership, architecture reviews, "
                "mentoring, and cross-team technical direction."
            ),
            "location": "Atlanta, GA",
            "work_model": "Hybrid",
            "decision": "Apply",
            "fit_score": 86,
            "url": "https://example.com/lead-modernization",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("medium", review.overqualified_risk)
        self.assertEqual("senior_lead_execution_forward", review.resume_positioning)
        self.assertTrue(review.should_generate_resume)
        self.assertIn("Lead title may still appear below the candidate's current Senior Principal level", review.seniority_scope_risks)

    def test_technical_director_routes_to_director_positioning(self) -> None:
        job = {
            "company": "DirectorCo",
            "title": "Director, Application Development",
            "description": (
                "Set application architecture direction and lead platform modernization, "
                "engineering execution, and production reliability for Java services."
            ),
            "decision": "Apply",
            "fit_score": 90,
            "url": "https://example.com/technical-director",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("low", review.overqualified_risk)
        self.assertEqual("director_technical_leadership", review.resume_positioning)
        self.assertTrue(review.should_generate_resume)

    def test_administrative_director_without_technical_ownership_is_rejected(self) -> None:
        job = {
            "company": "AdminCo",
            "title": "Director, Software Engineering",
            "description": "Own budgets, vendor contracts, staffing plans, and Agile program reporting.",
            "decision": "Apply",
            "fit_score": 90,
            "url": "https://example.com/admin-director",
        }

        review = classify_review(job=job, workflow_profile=PROFILE)

        self.assertEqual("SKIP", review.workflow_decision)
        self.assertFalse(review.should_generate_resume)
        self.assertIn("director role lacks clear technical ownership", review.hard_blockers)

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
        self.assertEqual("medium", review.overqualified_risk)
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
        self.assertEqual("technical_success", review.recommended_resume_base)

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

        self.assertEqual("consulting_architecture", review.recommended_resume_base)
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
