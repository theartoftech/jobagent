from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "discover_jobs_sharded.py"
SPEC = importlib.util.spec_from_file_location("discover_jobs_sharded", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {MODULE_PATH}")

discover_jobs_sharded = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = discover_jobs_sharded
SPEC.loader.exec_module(discover_jobs_sharded)


class ShardPlanningTests(unittest.TestCase):
    def test_default_freshness_window_is_sixty_days(self) -> None:
        self.assertEqual(discover_jobs_sharded.DEFAULT_MAX_AGE_DAYS, 60)

    def test_plan_company_shards_balances_heavy_companies(self) -> None:
        companies = [
            {"name": "Heavy A", "careers_url": "https://a.myworkdayjobs.com/en-US/Careers"},
            {"name": "Heavy B", "careers_url": "https://b.myworkdayjobs.com/en-US/Careers"},
            {"name": "Heavy C", "careers_url": "https://c.myworkdayjobs.com/en-US/Careers"},
            {"name": "Greenhouse", "careers_url": "https://boards.greenhouse.io/example"},
            {"name": "Lever", "careers_url": "https://jobs.lever.co/example"},
            {"name": "Light", "careers_url": "https://example.com/careers"},
        ]

        shards = discover_jobs_sharded.plan_company_shards(
            companies=companies,
            shard_count=3,
            max_companies_per_shard=20,
        )

        self.assertEqual(len(shards), 3)
        heavy_counts = [
            sum(1 for company in shard.companies if discover_jobs_sharded.company_adapter_name(company) == "workday")
            for shard in shards
        ]
        self.assertEqual(sorted(heavy_counts), [1, 1, 1])

    def test_plan_company_shards_respects_max_companies_per_shard(self) -> None:
        companies = [
            {"name": f"Company {index}", "careers_url": f"https://example{index}.com/careers"}
            for index in range(7)
        ]

        shards = discover_jobs_sharded.plan_company_shards(
            companies=companies,
            shard_count=3,
            max_companies_per_shard=3,
        )

        self.assertTrue(all(len(shard.companies) <= 3 for shard in shards))

    def test_select_startup_rotation_excludes_base_companies_and_is_date_deterministic(self) -> None:
        startups = [
            {"name": f"Startup {index}", "careers_url": f"https://startup{index}.example/jobs"}
            for index in range(6)
        ]
        base_companies = [
            {"name": "Startup 1", "careers_url": "https://existing.example/jobs"},
        ]

        first = discover_jobs_sharded.select_startup_rotation(
            startups=startups,
            base_companies=base_companies,
            today=discover_jobs_sharded.dt.date(2026, 7, 23),
            count=3,
        )
        repeated = discover_jobs_sharded.select_startup_rotation(
            startups=startups,
            base_companies=base_companies,
            today=discover_jobs_sharded.dt.date(2026, 7, 23),
            count=3,
        )
        next_day = discover_jobs_sharded.select_startup_rotation(
            startups=startups,
            base_companies=base_companies,
            today=discover_jobs_sharded.dt.date(2026, 7, 24),
            count=3,
        )

        self.assertEqual(first, repeated)
        self.assertEqual(len(first), 3)
        self.assertNotIn("Startup 1", {company["name"] for company in first})
        self.assertNotEqual(
            [company["name"] for company in first],
            [company["name"] for company in next_day],
        )

    def test_load_startup_watchlist_rejects_out_of_range_company_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "startup_watchlist.yml"
            path.write_text(
                "startups:\n  - name: Only Startup\n    careers_url: https://only.example/jobs\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "between 50 and 75"):
                discover_jobs_sharded.load_startup_watchlist(path)

    def test_repository_startup_watchlist_has_required_size_and_fields(self) -> None:
        root = Path(__file__).resolve().parents[1]

        startups = discover_jobs_sharded.load_startup_watchlist(root / "startup_watchlist.yml")

        self.assertGreaterEqual(len(startups), 50)
        self.assertLessEqual(len(startups), 75)
        self.assertTrue(all(company["name"] and company["careers_url"] for company in startups))
        self.assertEqual(len(startups), len({company["name"].casefold() for company in startups}))

    def test_shared_ats_board_companies_use_the_same_rotation_bucket(self) -> None:
        registry = {
            "southern company": ["https://southern.wd1.myworkdayjobs.com/External"],
            "georgia power": ["https://southern.wd1.myworkdayjobs.com/External"],
        }

        southern_bucket = discover_jobs_sharded.company_rotation_bucket(
            {"name": "Southern Company", "careers_url": "https://southerncompany.com/careers"},
            registry,
        )
        georgia_bucket = discover_jobs_sharded.company_rotation_bucket(
            {"name": "Georgia Power", "careers_url": "https://georgiapower.com/careers"},
            registry,
        )

        self.assertEqual(southern_bucket, georgia_bucket)

    def test_weekday_rotation_deep_scans_every_company_once(self) -> None:
        companies = [
            {"name": f"Company {index}", "careers_url": f"https://company{index}.example/jobs"}
            for index in range(50)
        ]
        selected_names: list[str] = []
        for day in range(3, 8):
            selected = discover_jobs_sharded.select_deep_scan_companies(
                companies=companies,
                today=discover_jobs_sharded.dt.date(2026, 8, day),
                ats_registry={},
                scan_state={},
            )
            selected_names.extend(company["name"] for company in selected)

        self.assertEqual(len(selected_names), len(companies))
        self.assertEqual(set(selected_names), {company["name"] for company in companies})

    def test_rotation_balances_unique_company_groups_across_weekdays(self) -> None:
        companies = [
            {"name": f"Company {index}", "careers_url": f"https://company{index}.example/jobs"}
            for index in range(136)
        ]

        buckets = discover_jobs_sharded.build_rotation_bucket_map(companies, {})
        counts = [sum(1 for bucket in buckets.values() if bucket == day) for day in range(5)]

        self.assertLessEqual(max(counts) - min(counts), 1)

    def test_overdue_company_is_added_outside_its_rotation_day(self) -> None:
        company = {"name": "Overdue Co", "careers_url": "https://overdue.example/jobs"}
        bucket = discover_jobs_sharded.build_rotation_bucket_map([company], {})[
            discover_jobs_sharded.company_rotation_key(company, {})
        ]
        today = discover_jobs_sharded.dt.date(2026, 8, 3)
        while today.weekday() == bucket:
            today += discover_jobs_sharded.dt.timedelta(days=1)
        scan_state = {
            "companies": {
                "overdue co": {
                    "last_successful_deep_scan": (today - discover_jobs_sharded.dt.timedelta(days=8)).isoformat()
                }
            }
        }

        selected = discover_jobs_sharded.select_deep_scan_companies(
            companies=[company],
            today=today,
            ats_registry={},
            scan_state=scan_state,
        )

        self.assertEqual(selected, [company])

    def test_same_day_rerun_reuses_companies_attempted_earlier_that_day(self) -> None:
        companies = [
            {"name": "Scheduled Co", "careers_url": "https://scheduled.example/jobs"},
            {"name": "Earlier Overdue Co", "careers_url": "https://overdue.example/jobs"},
        ]
        today = discover_jobs_sharded.dt.date(2026, 9, 9)
        scan_state = {
            "companies": {
                "earlier overdue co": {
                    "last_attempted_deep_scan": today.isoformat(),
                    "last_successful_deep_scan": today.isoformat(),
                    "retry_next_weekday": False,
                }
            }
        }

        selected = discover_jobs_sharded.select_deep_scan_companies(
            companies=companies,
            today=today,
            ats_registry={},
            scan_state=scan_state,
        )

        self.assertIn(companies[1], selected)


class AtsSentinelTests(unittest.TestCase):
    def test_sentinel_dedupes_shared_boards_and_returns_only_new_relevant_jobs(self) -> None:
        companies = [
            {"name": "Southern Company", "careers_url": "https://southerncompany.com/careers"},
            {"name": "Georgia Power", "careers_url": "https://georgiapower.com/careers"},
        ]
        board_url = "https://southern.wd1.myworkdayjobs.com/External"
        registry = {
            "southern company": [board_url],
            "georgia power": [board_url],
        }
        jobs = [
            discover_jobs_sharded.discover_jobs.JobPosting(
                company="Southern Company",
                title="Principal Software Engineer",
                url="https://southerncompany.jobs/jobs/2",
                job_id="2",
                fit_score=80,
                decision="Apply",
            ),
            discover_jobs_sharded.discover_jobs.JobPosting(
                company="Southern Company",
                title="Accountant",
                url="https://southerncompany.jobs/jobs/3",
                job_id="3",
                fit_score=10,
                decision="Skip",
            ),
        ]
        previous_state = {"boards": {discover_jobs_sharded.sentinel_board_key(board_url): {"job_ids": ["1", "3"]}}}

        with mock.patch.object(
            discover_jobs_sharded.discover_jobs,
            "discover_ats_sentinel",
            return_value=jobs,
        ) as sentinel_mock:
            result = discover_jobs_sharded.run_ats_sentinel(
                companies=companies,
                profile={},
                today=discover_jobs_sharded.dt.date(2026, 8, 7),
                max_age_days=60,
                ats_registry=registry,
                previous_state=previous_state,
                max_workers=2,
            )

        sentinel_mock.assert_called_once()
        self.assertEqual([job.job_id for job in result.new_jobs], ["2"])
        self.assertEqual(result.statuses[0].new_job_count, 1)
        board_state = result.state["boards"][discover_jobs_sharded.sentinel_board_key(board_url)]
        self.assertEqual(board_state["job_ids"], ["2", "3"])

    def test_first_sentinel_observation_limits_initial_recent_candidates(self) -> None:
        company = {"name": "Example", "careers_url": "https://example.com/jobs"}
        board_url = "https://boards.greenhouse.io/example"
        registry = {"example": [board_url]}
        jobs = [
            discover_jobs_sharded.discover_jobs.JobPosting(
                company="Example",
                title=f"Principal Software Engineer {index}",
                url=f"https://boards.greenhouse.io/example/jobs/{index}",
                job_id=str(index),
                posted_age_days=1,
                fit_score=80,
                decision="Apply",
            )
            for index in range(10)
        ]

        with mock.patch.object(
            discover_jobs_sharded.discover_jobs,
            "discover_ats_sentinel",
            return_value=jobs,
        ):
            result = discover_jobs_sharded.run_ats_sentinel(
                companies=[company],
                profile={},
                today=discover_jobs_sharded.dt.date(2026, 8, 7),
                max_age_days=60,
                ats_registry=registry,
                previous_state={},
                max_workers=1,
            )

        self.assertEqual(
            len(result.new_jobs),
            discover_jobs_sharded.MAX_INITIAL_SENTINEL_JOBS_PER_BOARD,
        )

    def test_deep_scan_state_retries_failures_and_records_success(self) -> None:
        companies = [
            {"name": "Good Co", "careers_url": "https://good.example/jobs"},
            {"name": "Failed Co", "careers_url": "https://failed.example/jobs"},
        ]
        statuses = [
            discover_jobs_sharded.CompanyRunStatus(
                company="Good Co",
                adapter_name="greenhouse",
                status="completed",
                elapsed_seconds=1,
                job_count=1,
            ),
            discover_jobs_sharded.CompanyRunStatus(
                company="Failed Co",
                adapter_name="workday",
                status="completed",
                elapsed_seconds=2,
                job_count=0,
                adapter_error_count=1,
            ),
        ]

        state = discover_jobs_sharded.update_deep_scan_state(
            previous_state={},
            base_companies=companies,
            selected_companies=companies,
            company_statuses=statuses,
            today=discover_jobs_sharded.dt.date(2026, 8, 7),
            ats_registry={},
        )

        self.assertEqual(
            state["companies"]["good co"]["last_successful_deep_scan"],
            "2026-08-07",
        )
        self.assertFalse(state["companies"]["good co"]["retry_next_weekday"])
        self.assertTrue(state["companies"]["failed co"]["retry_next_weekday"])
        self.assertNotIn(
            "last_successful_deep_scan",
            state["companies"]["failed co"],
        )


class ShardRuntimeTests(unittest.TestCase):
    def test_run_shard_marks_remaining_companies_as_timed_out_before_start(self) -> None:
        companies = [
            {"name": "Company A", "careers_url": "https://a.example.com/careers"},
            {"name": "Company B", "careers_url": "https://b.example.com/careers"},
            {"name": "Company C", "careers_url": "https://c.example.com/careers"},
        ]
        shard = discover_jobs_sharded.CompanyShard(shard_id=1, companies=companies, total_weight=3)
        profile = {
            "title_targets": ["Senior Software Engineer"],
            "strong_keywords": ["java"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        monotonic_values = iter([0.0, 0.0, 3.0, 6.0, 6.0])

        def fake_discover_company(*args, **kwargs):
            del args, kwargs
            return []

        with (
            mock.patch.object(
                discover_jobs_sharded.discover_jobs,
                "discover_company",
                side_effect=fake_discover_company,
            ),
            mock.patch.object(
                discover_jobs_sharded.time,
                "monotonic",
                side_effect=lambda: next(monotonic_values),
            ),
        ):
            result = discover_jobs_sharded.run_company_shard(
                shard=shard,
                profile=profile,
                today=discover_jobs_sharded.dt.date(2026, 6, 25),
                max_age_days=30,
                pause_seconds=0.0,
                ats_only=False,
                per_shard_timeout_seconds=5,
            )

        self.assertTrue(result.timed_out)
        self.assertEqual(result.company_statuses[0].status, "completed")
        self.assertEqual(result.company_statuses[1].status, "timed_out_before_start")
        self.assertEqual(result.company_statuses[2].status, "timed_out_before_start")

    def test_merge_shard_results_combines_jobs_errors_and_timeout_companies(self) -> None:
        job = discover_jobs_sharded.discover_jobs.JobPosting(
            company="ExampleCo",
            title="Senior Software Engineer",
            url="https://example.com/jobs/1",
        )
        status = discover_jobs_sharded.CompanyRunStatus(
            company="ExampleCo",
            adapter_name="workday",
            status="timed_out_before_start",
            elapsed_seconds=0.0,
            job_count=0,
        )
        result = discover_jobs_sharded.ShardRunResult(
            shard_id=1,
            jobs=[job],
            errors=[discover_jobs_sharded.discover_jobs.DiscoveryError("ExampleCo", "workday", "timeout")],
            company_statuses=[status],
            timed_out=True,
            elapsed_seconds=10.0,
        )

        merged = discover_jobs_sharded.merge_shard_results([result])

        self.assertEqual(len(merged["jobs"]), 1)
        self.assertEqual(len(merged["errors"]), 1)
        self.assertEqual(len(merged["timed_out_companies"]), 1)

    def test_company_miss_reason_identifies_non_ats_zero_job_company(self) -> None:
        status = discover_jobs_sharded.CompanyRunStatus(
            company="Root Co",
            adapter_name="non-ats-root",
            status="completed",
            elapsed_seconds=0.5,
            job_count=0,
        )

        self.assertEqual(
            discover_jobs_sharded.company_miss_reason(status),
            "no_registry_mapping: no structured ATS adapter resolved from configured root URL.",
        )

    def test_run_shard_records_registry_and_filter_diagnostics(self) -> None:
        company = {"name": "ExampleCo", "careers_url": "https://example.com/careers"}
        shard = discover_jobs_sharded.CompanyShard(
            shard_id=1,
            companies=[company],
            total_weight=1,
        )
        jobs = [
            discover_jobs_sharded.discover_jobs.JobPosting(
                company="ExampleCo",
                title="Senior Software Engineer",
                url="https://example.wd1.myworkdayjobs.com/Careers/job/1",
                fit_reasons=["target title match"],
            ),
            discover_jobs_sharded.discover_jobs.JobPosting(
                company="ExampleCo",
                title="Old Principal Engineer",
                url="https://example.wd1.myworkdayjobs.com/Careers/job/2",
                risk_flags=["older than 60 days"],
            ),
        ]
        registry = {"exampleco": ["https://example.wd1.myworkdayjobs.com/Careers"]}

        with mock.patch.object(
            discover_jobs_sharded.discover_jobs,
            "discover_company",
            return_value=jobs,
        ):
            result = discover_jobs_sharded.run_company_shard(
                shard=shard,
                profile={},
                today=discover_jobs_sharded.dt.date(2026, 8, 7),
                max_age_days=60,
                pause_seconds=0,
                ats_only=False,
                per_shard_timeout_seconds=30,
                ats_registry=registry,
            )

        status = result.company_statuses[0]
        self.assertTrue(status.registry_mapping_used)
        self.assertEqual(status.boards_queried, ("https://example.wd1.myworkdayjobs.com/Careers",))
        self.assertEqual(status.job_count, 2)
        self.assertEqual(status.title_match_count, 1)
        self.assertEqual(status.freshness_reject_count, 1)
        self.assertEqual(status.adapter_error_count, 0)
        self.assertFalse(status.root_only)


    def test_collect_external_source_jobs_includes_usajobs(self) -> None:
        job = discover_jobs_sharded.discover_jobs.JobPosting(
            company="General Services Administration",
            title="IT Specialist",
            url="https://www.usajobs.gov/job/123",
            source="usajobs",
        )
        errors: list[discover_jobs_sharded.discover_jobs.DiscoveryError] = []
        profile = {
            "title_targets": ["IT Specialist"],
            "strong_keywords": ["java"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }

        with mock.patch.object(
            discover_jobs_sharded.discover_jobs,
            "discover_usajobs",
            return_value=[job],
        ) as discover_mock:
            jobs = discover_jobs_sharded.collect_external_source_jobs(
                profile=profile,
                today=discover_jobs_sharded.dt.date(2026, 7, 20),
                max_age_days=60,
                errors=errors,
            )

        self.assertEqual(jobs, [job])
        discover_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
