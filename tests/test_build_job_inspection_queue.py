from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_job_inspection_queue.py"
SPEC = importlib.util.spec_from_file_location("build_job_inspection_queue", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {MODULE_PATH}")

build_job_inspection_queue = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_job_inspection_queue
SPEC.loader.exec_module(build_job_inspection_queue)


class SelectJobBucketsTests(unittest.TestCase):
    def test_strict_review_filter_removes_rejected_and_applied_candidate_rows(self) -> None:
        apply_job = build_job_inspection_queue.job_from_dict(
            {
                "company": "Example Co",
                "title": "Principal Engineer",
                "job_id": "1",
                "posted_on": "2026-08-04",
                "posted_age_days": 0,
                "location": "Remote",
                "work_model": "Remote",
                "fit_score": 100,
                "decision": "Apply",
                "source": "greenhouse",
                "url": "https://example.com/jobs/1",
                "risk_flags": [],
            }
        )
        rejected_job = build_job_inspection_queue.job_from_dict(
            {
                "company": "Example Co",
                "title": "Platform Engineer",
                "job_id": "2",
                "posted_on": "2026-08-04",
                "posted_age_days": 0,
                "location": "Remote",
                "work_model": "Remote",
                "fit_score": 97,
                "decision": "Apply",
                "source": "greenhouse",
                "url": "https://example.com/jobs/2",
                "risk_flags": [],
            }
        )
        applied_job = build_job_inspection_queue.job_from_dict(
            {
                "company": "Example Co",
                "title": "Customer Engineer",
                "job_id": "3",
                "posted_on": "2026-08-04",
                "posted_age_days": 0,
                "location": "Remote",
                "work_model": "Remote",
                "fit_score": 93,
                "decision": "Apply",
                "source": "greenhouse",
                "url": "https://example.com/jobs/3",
                "risk_flags": [],
            }
        )
        buckets = build_job_inspection_queue.JobBuckets(
            apply_jobs=[apply_job, rejected_job, applied_job],
            maybe_jobs=[],
            needs_freshness_verification=[],
        )
        statuses = {
            build_job_inspection_queue.job_identity_key(rejected_job): ("SKIP", False),
            build_job_inspection_queue.job_identity_key(applied_job): ("STRONG APPLY", True),
        }

        filtered = build_job_inspection_queue.filter_buckets_by_strict_review(buckets, statuses)

        self.assertEqual([apply_job], filtered.apply_jobs)

    def test_carry_forward_uses_only_active_jobs_from_latest_strict_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            (out_dir / "2026-07-31-workflow-review.json").write_text(
                """{
                  "reviews": [
                    {
                      "company": "Example Co",
                      "title": "Principal Software Engineer",
                      "url": "https://example.com/jobs/active",
                      "workflow_decision": "APPLY",
                      "should_generate_resume": true,
                      "gap_questions_needed": false,
                      "already_applied": false
                    },
                    {
                      "company": "Example Co",
                      "title": "Rejected Engineer",
                      "url": "https://example.com/jobs/rejected",
                      "workflow_decision": "SKIP",
                      "should_generate_resume": false,
                      "gap_questions_needed": false,
                      "already_applied": false
                    },
                    {
                      "company": "Example Co",
                      "title": "Applied Engineer",
                      "url": "https://example.com/jobs/applied",
                      "workflow_decision": "STRONG APPLY",
                      "should_generate_resume": true,
                      "gap_questions_needed": false,
                      "already_applied": true
                    }
                  ],
                  "source_jobs": [
                    {
                      "company": "Example Co",
                      "title": "Principal Software Engineer",
                      "job_id": "ACTIVE-1",
                      "posted_on": "2026-07-30",
                      "posted_age_days": 1,
                      "location": "Remote",
                      "work_model": "Remote",
                      "fit_score": 90,
                      "decision": "Apply",
                      "source": "workday",
                      "url": "https://example.com/jobs/active",
                      "risk_flags": []
                    },
                    {
                      "company": "Example Co",
                      "title": "Rejected Engineer",
                      "job_id": "REJECTED-1",
                      "posted_on": "2026-07-30",
                      "posted_age_days": 1,
                      "location": "Remote",
                      "work_model": "Remote",
                      "fit_score": 90,
                      "decision": "Apply",
                      "source": "workday",
                      "url": "https://example.com/jobs/rejected",
                      "risk_flags": []
                    },
                    {
                      "company": "Example Co",
                      "title": "Applied Engineer",
                      "job_id": "APPLIED-1",
                      "posted_on": "2026-07-30",
                      "posted_age_days": 1,
                      "location": "Remote",
                      "work_model": "Remote",
                      "fit_score": 90,
                      "decision": "Apply",
                      "source": "workday",
                      "url": "https://example.com/jobs/applied",
                      "risk_flags": []
                    }
                  ]
                }""",
                encoding="utf-8",
            )

            jobs = build_job_inspection_queue.load_carry_forward_jobs(out_dir, "2026-08-04")

        self.assertEqual(["Principal Software Engineer"], [job.title for job in jobs])

    def test_select_job_buckets_does_not_repeat_posting_in_apply_and_maybe(self) -> None:
        jobs = [
            {
                "company": "Example Co",
                "title": "Principal Software Engineer",
                "job_id": "REQ-1",
                "posted_on": "2026-08-04",
                "posted_age_days": 0,
                "location": "Remote",
                "work_model": "Remote",
                "fit_score": 90,
                "decision": "Apply",
                "source": "workday",
                "url": "https://example.com/jobs/1",
                "risk_flags": [],
            },
            {
                "company": "Example Co",
                "title": "Principal Software Engineer",
                "job_id": "REQ-1",
                "posted_on": "2026-08-03",
                "posted_age_days": 1,
                "location": "Remote",
                "work_model": "Remote",
                "fit_score": 70,
                "decision": "Maybe",
                "source": "public-board",
                "url": "https://example.com/jobs/1",
                "risk_flags": [],
            },
        ]

        buckets = build_job_inspection_queue.select_job_buckets(jobs)

        self.assertEqual(["Principal Software Engineer"], [job.title for job in buckets.apply_jobs])
        self.assertEqual([], buckets.maybe_jobs)

    def test_queue_caps_are_widened(self) -> None:
        self.assertEqual(build_job_inspection_queue.MAX_APPLY_ROWS_PER_COMPANY, 5)
        self.assertEqual(build_job_inspection_queue.MAX_MAYBE_ROWS_PER_COMPANY, 8)
        self.assertEqual(build_job_inspection_queue.MAX_TOTAL_MAYBE_ROWS, 50)

    def test_select_job_buckets_preserves_apply_rows_and_dedupes_titles(self) -> None:
        jobs = [
            {
                "company": "Example Co",
                "title": "Senior Software Engineer",
                "job_id": "REQ-1",
                "posted_on": "2026-06-26",
                "posted_age_days": 0,
                "location": "Remote - United States",
                "work_model": "Remote",
                "fit_score": 82,
                "decision": "Apply",
                "source": "workday",
                "url": "https://example.com/jobs/1",
                "risk_flags": [],
            },
            {
                "company": "Example Co",
                "title": "Senior Software Engineer",
                "job_id": "REQ-1-DUPE",
                "posted_on": "2026-06-25",
                "posted_age_days": 1,
                "location": "Remote - United States",
                "work_model": "Remote",
                "fit_score": 78,
                "decision": "Apply",
                "source": "workday",
                "url": "https://example.com/jobs/1-duplicate",
                "risk_flags": [],
            },
            {
                "company": "Example Co",
                "title": "Principal Software Engineer",
                "job_id": "REQ-2",
                "posted_on": "2026-06-24",
                "posted_age_days": 2,
                "location": "Atlanta, GA",
                "work_model": "Hybrid",
                "fit_score": 76,
                "decision": "Apply",
                "source": "greenhouse",
                "url": "https://example.com/jobs/2",
                "risk_flags": [],
            },
            {
                "company": "Example Co",
                "title": "Technical Consultant",
                "job_id": "REQ-3",
                "posted_on": "2026-06-23",
                "posted_age_days": 3,
                "location": "Remote - United States",
                "work_model": "Remote",
                "fit_score": 72,
                "decision": "Apply",
                "source": "workday",
                "url": "https://example.com/jobs/3",
                "risk_flags": [],
            },
            {
                "company": "Example Co",
                "title": "Lead Software Engineer",
                "job_id": "REQ-4",
                "posted_on": "2026-06-22",
                "posted_age_days": 4,
                "location": "Remote - United States",
                "work_model": "Remote",
                "fit_score": 70,
                "decision": "Apply",
                "source": "workday",
                "url": "https://example.com/jobs/4",
                "risk_flags": [],
            },
        ]

        buckets = build_job_inspection_queue.select_job_buckets(jobs)

        self.assertEqual(
            [job.title for job in buckets.apply_jobs],
            [
                "Senior Software Engineer",
                "Principal Software Engineer",
            ],
        )
        self.assertEqual(
            [job.title for job in buckets.maybe_jobs],
            [
                "Technical Consultant",
                "Lead Software Engineer",
            ],
        )

    def test_select_job_buckets_dedupes_same_official_url_with_changed_title(self) -> None:
        jobs = [
            {
                "company": "Cohere Health",
                "title": "Senior Software Engineer",
                "job_id": "7771934003",
                "posted_on": "2026-07-21",
                "posted_age_days": 1,
                "location": "United States",
                "work_model": "Remote",
                "fit_score": 88,
                "decision": "Apply",
                "source": "greenhouse",
                "url": "https://job-boards.greenhouse.io/coherehealth/jobs/7771934003",
                "risk_flags": [],
            },
            {
                "company": "Cohere Health",
                "title": "Senior Software Engineer, Client Implementations",
                "job_id": "",
                "posted_on": "2026-07-08",
                "posted_age_days": None,
                "location": "United States",
                "work_model": "Remote",
                "fit_score": 88,
                "decision": "Apply",
                "source": "carry-forward",
                "url": "https://job-boards.greenhouse.io/coherehealth/jobs/7771934003",
                "risk_flags": ["carried forward from prior daily report"],
            },
        ]

        buckets = build_job_inspection_queue.select_job_buckets(jobs)

        self.assertEqual(len(buckets.apply_jobs), 1)
        self.assertEqual(buckets.apply_jobs[0].title, "Senior Software Engineer")

    def test_current_discovery_supersedes_higher_scored_carry_forward_same_posting(self) -> None:
        current = build_job_inspection_queue.job_from_dict(
            {
                "company": "Cohere Health",
                "title": "Sr. Software Engineer",
                "job_id": "7771934003",
                "posted_on": "2026-07-30",
                "posted_age_days": 5,
                "location": "United States",
                "work_model": "Remote",
                "fit_score": 31,
                "decision": "Skip",
                "source": "greenhouse",
                "url": "https://job-boards.greenhouse.io/coherehealth/jobs/7771934003",
                "risk_flags": ["hard-stack requirement gaps"],
            }
        )
        carry_forward = build_job_inspection_queue.job_from_dict(
            {
                "company": "Cohere Health",
                "title": "Senior Software Engineer, Client Implementations",
                "job_id": "",
                "posted_on": "2026-07-08",
                "posted_age_days": None,
                "location": "United States",
                "work_model": "Remote",
                "fit_score": 100,
                "decision": "Apply",
                "source": "carry-forward",
                "url": "https://job-boards.greenhouse.io/coherehealth/jobs/7771934003",
                "risk_flags": ["carried forward from prior daily report"],
            }
        )

        merged = build_job_inspection_queue.merge_current_and_carry_forward_jobs(
            [current],
            [carry_forward],
        )

        self.assertEqual([current], merged)
        self.assertEqual([], build_job_inspection_queue.select_job_buckets(merged).apply_jobs)

    def test_select_job_buckets_limits_maybe_rows_per_company_and_overall(self) -> None:
        jobs: list[dict[str, object]] = []
        for index in range(8):
            jobs.append(
                {
                    "company": "Example Co",
                    "title": f"Maybe Role {index}",
                    "job_id": f"MAYBE-{index}",
                    "posted_on": "2026-06-24",
                    "posted_age_days": index % 5,
                    "location": "Remote - United States",
                    "work_model": "Remote",
                    "fit_score": 64 - index,
                    "decision": "Maybe",
                    "source": "workday",
                    "url": f"https://example.com/jobs/maybe-{index}",
                    "risk_flags": [],
                }
            )

        buckets = build_job_inspection_queue.select_job_buckets(jobs)

        self.assertEqual(len(buckets.maybe_jobs), build_job_inspection_queue.MAX_MAYBE_ROWS_PER_COMPANY)
        self.assertEqual(buckets.maybe_jobs[0].title, "Maybe Role 0")
        self.assertEqual(buckets.maybe_jobs[-1].title, "Maybe Role 7")

    def test_select_job_buckets_keeps_strong_unknown_date_roles_in_main_queue(self) -> None:
        jobs = [
            {
                "company": "Example Co",
                "title": "Senior Software Engineer",
                "job_id": "REQ-10",
                "posted_on": "",
                "posted_age_days": None,
                "location": "Remote - United States",
                "work_model": "Remote",
                "fit_score": 78,
                "decision": "Apply",
                "source": "public-board:LinkedIn Jobs->workday",
                "url": "https://example.com/jobs/10",
                "risk_flags": [],
            },
            {
                "company": "Another Co",
                "title": "Staff Engineer",
                "job_id": "REQ-11",
                "posted_on": "",
                "posted_age_days": None,
                "location": "Remote - United States",
                "work_model": "Remote",
                "fit_score": 49,
                "decision": "Maybe",
                "source": "public-board:Indeed->greenhouse",
                "url": "https://example.com/jobs/11",
                "risk_flags": [],
            },
        ]

        buckets = build_job_inspection_queue.select_job_buckets(jobs)

        self.assertEqual([job.company for job in buckets.apply_jobs], ["Example Co"])
        self.assertEqual([job.company for job in buckets.maybe_jobs], ["Another Co"])
        self.assertEqual(buckets.needs_freshness_verification, [])

    def test_select_job_buckets_routes_live_evergreen_role_to_freshness_verification(self) -> None:
        jobs = [
            {
                "company": "Example Co",
                "title": "Staff Backend Software Engineer",
                "job_id": "9274",
                "posted_on": "2025-02-21",
                "posted_age_days": 518,
                "location": "Atlanta, GA",
                "work_model": "Hybrid",
                "fit_score": 82,
                "decision": "Needs Freshness Verification",
                "source": "intuit",
                "url": "https://example.com/jobs/9274",
                "risk_flags": [
                    "official posting is live but older than 60 days; needs freshness verification"
                ],
            }
        ]

        buckets = build_job_inspection_queue.select_job_buckets(jobs)

        self.assertEqual([job.job_id for job in buckets.needs_freshness_verification], ["9274"])

    def test_select_job_buckets_excludes_low_fit_roles_from_freshness_verification(self) -> None:
        jobs = [
            {
                "company": "Example Co",
                "title": "Talent Community",
                "job_id": "LOW-1",
                "posted_on": "2025-02-21",
                "posted_age_days": 518,
                "location": "Remote",
                "work_model": "Remote",
                "fit_score": 19,
                "decision": "Needs Freshness Verification",
                "source": "greenhouse",
                "url": "https://example.com/jobs/low-1",
                "risk_flags": ["official posting is live but older than 60 days"],
            }
        ]

        buckets = build_job_inspection_queue.select_job_buckets(jobs)

        self.assertEqual([], buckets.needs_freshness_verification)


class WriteInspectionQueueTests(unittest.TestCase):
    def test_preserved_human_search_section_excludes_stale_networking_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            report_path = Path(tmp_dir) / "2026-08-04-daily-job-search.md"
            report_path.write_text(
                "\n".join(
                    [
                        "# Daily Job Search Report - 2026-08-04",
                        "",
                        "## Human Search Calibration",
                        "",
                        "1. Example Co - https://example.com/careers",
                        "",
                        "<!-- networking-pass:start -->",
                        "## People to Contact",
                        "",
                        "### CVS Health",
                        "<!-- networking-pass:end -->",
                    ]
                ),
                encoding="utf-8",
            )

            preserved = build_job_inspection_queue.preserved_human_search_section(report_path)

        self.assertEqual(
            [
                "## Human Search Calibration",
                "",
                "1. Example Co - https://example.com/careers",
                "",
            ],
            preserved,
        )

    def test_write_daily_report_from_buckets_syncs_apply_and_maybe_rows(self) -> None:
        apply_job = build_job_inspection_queue.InspectionJob(
            company="Example Co",
            title="Senior Software Engineer",
            job_id="REQ-1",
            posted_on="2026-07-23",
            posted_age_days=0,
            location="Remote - United States",
            work_model="Remote",
            fit_score=82,
            decision="Apply",
            source="workday",
            url="https://example.com/jobs/1",
            risk_flags=(),
        )
        maybe_job = build_job_inspection_queue.InspectionJob(
            company="Example Co",
            title="Technical Consultant",
            job_id="REQ-2",
            posted_on="2026-07-20",
            posted_age_days=3,
            location="Atlanta, GA",
            work_model="Hybrid",
            fit_score=70,
            decision="Maybe",
            source="workday",
            url="https://example.com/jobs/2",
            risk_flags=("work model needs clarification",),
        )
        buckets = build_job_inspection_queue.JobBuckets(
            apply_jobs=[apply_job],
            maybe_jobs=[maybe_job],
            needs_freshness_verification=[],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            report_path = Path(tmp_dir) / "2026-07-23-daily-job-search.md"
            build_job_inspection_queue.write_daily_report_from_buckets(
                buckets=buckets,
                report_path=report_path,
                run_date="2026-07-23",
            )
            markdown = report_path.read_text(encoding="utf-8")

        self.assertIn("# Daily Job Search Report - 2026-07-23", markdown)
        self.assertIn("## Discovery Candidates — Higher Signal", markdown)
        self.assertIn("## Discovery Candidates — Review Signal", markdown)
        self.assertNotIn("## Apply\n", markdown)
        self.assertNotIn("## Maybe\n", markdown)
        self.assertIn("Senior Software Engineer", markdown)
        self.assertIn("Technical Consultant", markdown)
        self.assertIn("https://example.com/jobs/1 | No |", markdown)
        self.assertIn("https://example.com/jobs/2 | No |", markdown)
        self.assertIn("## Needs Freshness Verification", markdown)

    def test_write_inspection_queue_emits_all_sections(self) -> None:
        jobs = [
            {
                "company": "Example Co",
                "title": "Senior Software Engineer",
                "job_id": "REQ-1",
                "posted_on": "2026-06-26",
                "posted_age_days": 0,
                "location": "Remote - United States",
                "work_model": "Remote",
                "fit_score": 82,
                "decision": "Apply",
                "source": "workday",
                "url": "https://example.com/jobs/1",
                "risk_flags": [],
            },
            {
                "company": "Another Co",
                "title": "Technical Consultant",
                "job_id": "REQ-2",
                "posted_on": "2026-06-25",
                "posted_age_days": 1,
                "location": "Atlanta, GA",
                "work_model": "Hybrid",
                "fit_score": 61,
                "decision": "Maybe",
                "source": "greenhouse",
                "url": "https://example.com/jobs/2",
                "risk_flags": ["nonlocal Flex/Hybrid role needs office-attendance clarification"],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            discovered_jobs_path = out_dir / "2026-06-27-discovered-jobs.json"
            discovered_jobs_path.write_text(
                build_job_inspection_queue.json.dumps(
                    {
                        "generated_on": "2026-06-27",
                        "max_age_days": 30,
                        "errors": [],
                        "jobs": jobs,
                    }
                ),
                encoding="utf-8",
            )

            output_path = build_job_inspection_queue.write_inspection_queue(
                discovered_jobs_path=discovered_jobs_path,
                out_dir=out_dir,
                run_date="2026-06-27",
            )

            markdown = output_path.read_text(encoding="utf-8")

        self.assertIn("# 2026-06-27 - Job Inspection Queue", markdown)
        self.assertIn("## Discovery Candidates — Higher Signal", markdown)
        self.assertIn("## Discovery Candidates — Review Signal", markdown)
        self.assertIn("## Needs Freshness Verification", markdown)
        self.assertIn("No roles need freshness verification", markdown)
        self.assertIn("Senior Software Engineer", markdown)
        self.assertIn("Technical Consultant", markdown)
        self.assertNotIn("Principal Software Engineer", markdown)

    def test_write_inspection_queue_carries_forward_active_strict_workflow_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            discovered_jobs_path = out_dir / "2026-07-09-discovered-jobs.json"
            discovered_jobs_path.write_text(
                build_job_inspection_queue.json.dumps(
                    {
                        "generated_on": "2026-07-09",
                        "max_age_days": 45,
                        "errors": [],
                        "jobs": [],
                    }
                ),
                encoding="utf-8",
            )
            (out_dir / "2026-07-08-workflow-review.json").write_text(
                """{
                  "reviews": [{
                    "company": "Carry Co",
                    "title": "Senior Technical Consultant",
                    "url": "https://carry.example/jobs/1",
                    "workflow_decision": "APPLY",
                    "should_generate_resume": true,
                    "gap_questions_needed": false,
                    "already_applied": false
                  }],
                  "source_jobs": [{
                    "company": "Carry Co",
                    "title": "Senior Technical Consultant",
                    "job_id": "CARRY-1",
                    "posted_on": "2026-07-07",
                    "posted_age_days": 2,
                    "location": "Remote US",
                    "work_model": "Remote",
                    "fit_score": 77,
                    "decision": "Apply",
                    "source": "workday",
                    "url": "https://carry.example/jobs/1",
                    "risk_flags": []
                  }]
                }""",
                encoding="utf-8",
            )

            output_path = build_job_inspection_queue.write_inspection_queue(
                discovered_jobs_path=discovered_jobs_path,
                out_dir=out_dir,
                run_date="2026-07-09",
            )
            markdown = output_path.read_text(encoding="utf-8")

        self.assertIn("Senior Technical Consultant", markdown)
        self.assertNotIn("Solution Architect", markdown)
        self.assertIn("workday", markdown)
        self.assertNotIn("Done Co", markdown)
        self.assertIn("No roles need freshness verification", markdown)

    def test_write_inspection_queue_filters_carry_forward_rows_matching_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            discovered_jobs_path = out_dir / "2026-07-16-discovered-jobs.json"
            discovered_jobs_path.write_text(
                build_job_inspection_queue.json.dumps(
                    {
                        "generated_on": "2026-07-16",
                        "max_age_days": 60,
                        "errors": [],
                        "jobs": [],
                    }
                ),
                encoding="utf-8",
            )
            (out_dir / "2026-07-15-workflow-review.json").write_text(
                """{
                  "reviews": [{
                    "company": "NCR Voyix",
                    "title": "Senior Software Engineer",
                    "url": "https://ncr.wd1.myworkdayjobs.com/ext_us/job/ATLANTA-GA-USA/Senior-Software-Engineer_R0157400",
                    "workflow_decision": "APPLY",
                    "should_generate_resume": true,
                    "gap_questions_needed": false,
                    "already_applied": false
                  }],
                  "source_jobs": [{
                    "company": "NCR Voyix",
                    "title": "Senior Software Engineer",
                    "job_id": "R0157400",
                    "posted_on": "2026-06-09",
                    "posted_age_days": 36,
                    "location": "ATLANTA, GA",
                    "work_model": "Hybrid",
                    "fit_score": 98,
                    "decision": "Apply",
                    "source": "workday",
                    "url": "https://ncr.wd1.myworkdayjobs.com/ext_us/job/ATLANTA-GA-USA/Senior-Software-Engineer_R0157400",
                    "risk_flags": []
                  }]
                }""",
                encoding="utf-8",
            )
            overrides_path = out_dir / "job_overrides.yml"
            overrides_path.write_text(
                "\n".join(
                    [
                        "overrides:",
                        "  - url_contains: R0157400",
                        "    decision: Skip",
                        "    reason: reviewed stretch",
                    ]
                ),
                encoding="utf-8",
            )

            output_path = build_job_inspection_queue.write_inspection_queue(
                discovered_jobs_path=discovered_jobs_path,
                out_dir=out_dir,
                run_date="2026-07-16",
                job_overrides_path=overrides_path,
            )
            markdown = output_path.read_text(encoding="utf-8")

        self.assertNotIn("R0157400", markdown)

    def test_filter_overridden_jobs_supports_exact_company_skip(self) -> None:
        temporal = build_job_inspection_queue.InspectionJob(
            company="Temporal Technologies",
            title="Staff Software Engineer",
            job_id="TEMP-1",
            posted_on="2026-08-26",
            posted_age_days=2,
            location="United States",
            work_model="Remote",
            fit_score=95,
            decision="Apply",
            source="greenhouse",
            url="https://example.com/temporal/TEMP-1",
            risk_flags=(),
        )
        partner = build_job_inspection_queue.InspectionJob(
            company="Temporal Technologies Partner",
            title="Staff Software Engineer",
            job_id="PARTNER-1",
            posted_on="2026-08-26",
            posted_age_days=2,
            location="United States",
            work_model="Remote",
            fit_score=95,
            decision="Apply",
            source="greenhouse",
            url="https://example.com/partner/PARTNER-1",
            risk_flags=(),
        )

        filtered = build_job_inspection_queue.filter_overridden_jobs(
            [temporal, partner],
            [{"company": "Temporal Technologies", "decision": "Skip"}],
        )

        self.assertEqual([partner], filtered)

    def test_filter_overridden_jobs_supports_title_contains_skip(self) -> None:
        pre_sales = build_job_inspection_queue.InspectionJob(
            company="Hightouch",
            title="Solutions Engineer, Enterprise East (Pre-Sales)",
            job_id="PRE-1",
            posted_on="2026-08-26",
            posted_age_days=2,
            location="United States",
            work_model="Remote",
            fit_score=90,
            decision="Apply",
            source="greenhouse",
            url="https://example.com/hightouch/PRE-1",
            risk_flags=(),
        )

        filtered = build_job_inspection_queue.filter_overridden_jobs(
            [pre_sales],
            [{"title_contains": "pre-sales", "decision": "Skip"}],
        )

        self.assertEqual([], filtered)

    def test_filter_overridden_jobs_releases_expired_company_skip(self) -> None:
        block = build_job_inspection_queue.InspectionJob(
            company="Block",
            title="Staff Software Engineer",
            job_id="BLOCK-1",
            posted_on="2026-09-28",
            posted_age_days=0,
            location="United States",
            work_model="Remote",
            fit_score=95,
            decision="Apply",
            source="greenhouse",
            url="https://example.com/block/BLOCK-1",
            risk_flags=(),
        )
        overrides = [
            {
                "company": "Block",
                "decision": "Skip",
                "expires_on": "2026-10-28",
            }
        ]

        active = build_job_inspection_queue.filter_overridden_jobs(
            [block], overrides, as_of=build_job_inspection_queue.dt.date(2026, 10, 28)
        )
        expired = build_job_inspection_queue.filter_overridden_jobs(
            [block], overrides, as_of=build_job_inspection_queue.dt.date(2026, 10, 29)
        )

        self.assertEqual([], active)
        self.assertEqual([block], expired)


if __name__ == "__main__":
    unittest.main()
