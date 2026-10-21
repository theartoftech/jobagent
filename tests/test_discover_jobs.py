from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import URLError


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "discover_jobs.py"
SPEC = importlib.util.spec_from_file_location("discover_jobs", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {MODULE_PATH}")

discover_jobs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = discover_jobs
SPEC.loader.exec_module(discover_jobs)


class BuildSearchTermsTests(unittest.TestCase):
    def test_default_freshness_window_is_sixty_days(self) -> None:
        self.assertEqual(discover_jobs.DEFAULT_MAX_AGE_DAYS, 60)

    def test_build_search_terms_prioritizes_titles_and_dedupes_case_insensitively(self) -> None:
        profile = {
            "title_targets": [
                "Senior Software Engineer",
                "Technical Consultant",
                "senior software engineer",
                "Solution Architect",
            ]
        }

        terms = discover_jobs.build_search_terms(profile)

        self.assertEqual(
            terms[:5],
            [
                "Senior Software Engineer",
                "Technical Consultant",
                "Solution Architect",
                "software engineer",
                "principal software engineer",
            ],
        )
        self.assertEqual(terms.count("Senior Software Engineer"), 1)
        self.assertNotIn("senior software engineer", terms)

    def test_build_search_terms_includes_company_title_aliases(self) -> None:
        profile = {
            "title_targets": [
                "Senior Software Engineer",
                "Technical Consultant",
            ]
        }
        company = {
            "name": "CVS Health",
            "title_aliases": [
                "Software Development Engineer",
                "Staff Software Development Engineer",
                "technical consultant",
            ],
        }

        terms = discover_jobs.build_search_terms(profile, company)

        self.assertIn("Software Development Engineer", terms[:6])
        self.assertIn("Staff Software Development Engineer", terms[:8])
        self.assertEqual(
            sum(1 for term in terms if term.casefold() == "technical consultant"),
            1,
        )

    def test_build_search_terms_prioritizes_company_aliases_with_long_title_lists(self) -> None:
        profile = {
            "title_targets": [f"Title {index}" for index in range(40)]
        }
        company = {
            "name": "CVS Health",
            "title_aliases": [
                "Software Development Engineer",
                "Staff Software Development Engineer",
            ],
        }

        terms = discover_jobs.build_search_terms(profile, company)

        self.assertEqual(
            terms[:2],
            ["Staff Software Development Engineer", "Software Development Engineer"],
        )
        self.assertIn("Title 0", terms)

    def test_max_search_terms_per_company_leaves_room_for_extended_title_set(self) -> None:
        self.assertGreaterEqual(discover_jobs.MAX_SEARCH_TERMS_PER_COMPANY, 24)

    def test_worth_applying_titles_are_scored_and_searched_after_best_fit_titles(self) -> None:
        profile = {
            "title_targets": [
                "Senior Principal Software Engineer",
                "Principal Software Engineer",
            ],
            "worth_applying_title_targets": [
                "Director of Software Engineering",
                "Director, Architecture",
            ],
        }

        terms = discover_jobs.build_search_terms(profile)
        score_targets = discover_jobs.build_title_target_set(profile, None)

        self.assertEqual(
            terms[:2],
            ["Senior Principal Software Engineer", "Principal Software Engineer"],
        )
        self.assertIn("Director of Software Engineering", terms)
        self.assertIn("director, architecture", score_targets)

    def test_profile_contains_requested_best_fit_and_director_lanes(self) -> None:
        profile_document = discover_jobs.load_yaml(
            Path(__file__).resolve().parents[1] / "candidate_profile.yml"
        )
        self.assertIsInstance(profile_document, dict)
        profile = profile_document["target_profile"]

        self.assertIsInstance(profile, dict)
        self.assertTrue(
            {
                "Senior Principal Software Engineer",
                "Principal Application Engineer",
                "Lead Architect",
                "Technical Director",
            }.issubset(set(profile["title_targets"]))
        )
        self.assertTrue(
            {
                "Director of Software Engineering",
                "Director, Application Development",
                "Director, Platform Engineering",
                "Director, Enterprise Applications",
                "Director, Architecture",
                "Director, Technical Delivery",
            }.issubset(set(profile["worth_applying_title_targets"]))
        )

    def test_profile_contains_forward_deployed_engineering_lane(self) -> None:
        profile_document = discover_jobs.load_yaml(
            Path(__file__).resolve().parents[1] / "candidate_profile.yml"
        )
        self.assertIsInstance(profile_document, dict)
        profile = profile_document["target_profile"]

        self.assertTrue(
            {
                "Forward Deployed Engineer",
                "Forward Deployed Software Engineer",
                "Senior Forward Deployed Engineer",
                "Principal Forward Deployed Engineer",
                "Forward Deployed Architect",
                "Forward Deployed Solutions Architect",
            }.issubset(set(profile["title_targets"]))
        )

    def test_social_default_time_budget_is_five_minutes(self) -> None:
        self.assertEqual(discover_jobs.X_POST_TIME_BUDGET_SECONDS, 300)

    def test_social_fast_fail_defaults_are_configured(self) -> None:
        self.assertEqual(discover_jobs.MAX_SOCIAL_CONSECUTIVE_QUERY_FAILURES, 5)
        self.assertEqual(discover_jobs.MAX_SOCIAL_TOTAL_QUERY_FAILURES, 15)

    def test_parse_posted_age_accepts_month_name_dates(self) -> None:
        posted_on, age, date_basis = discover_jobs.parse_posted_age(
            "Posted Jun 18, 2026",
            discover_jobs.dt.date(2026, 7, 7),
        )

        self.assertEqual(posted_on, "2026-06-18")
        self.assertEqual(age, 19)
        self.assertEqual(date_basis, "Posted Jun 18, 2026")

    def test_parse_posted_age_accepts_non_padded_iso_dates(self) -> None:
        posted_on, age, date_basis = discover_jobs.parse_posted_age(
            "2025-2-21",
            discover_jobs.dt.date(2026, 7, 24),
        )

        self.assertEqual(posted_on, "2025-02-21")
        self.assertEqual(age, 518)
        self.assertEqual(date_basis, "2025-2-21")


class CurlTimeoutTests(unittest.TestCase):
    def test_request_json_redacts_headers_from_curl_failure(self) -> None:
        failure = subprocess.CalledProcessError(
            6,
            [
                "curl",
                "-H",
                "Authorization-Key: top-secret-value",
                "https://example.com/api",
            ],
        )

        with (
            mock.patch.object(discover_jobs.shutil, "which", return_value="/usr/bin/curl"),
            mock.patch.object(discover_jobs.subprocess, "run", side_effect=failure),
        ):
            with self.assertRaises(URLError) as raised:
                discover_jobs.request_json(
                    "https://example.com/api",
                    headers={"Authorization-Key": "top-secret-value"},
                )

        message = str(raised.exception)
        self.assertIn("exit code 6", message)
        self.assertNotIn("top-secret-value", message)
        self.assertNotIn("Authorization-Key", message)
        self.assertNotIn("curl", message)

    def test_request_json_sets_subprocess_timeout_padding(self) -> None:
        completed_process = mock.Mock(stdout='{"ok": true}')

        with (
            mock.patch.object(discover_jobs.shutil, "which", return_value="/usr/bin/curl"),
            mock.patch.object(discover_jobs.subprocess, "run", return_value=completed_process) as run_mock,
        ):
            result = discover_jobs.request_json("https://example.com/api", timeout=12)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            run_mock.call_args.kwargs["timeout"],
            12 + discover_jobs.SUBPROCESS_TIMEOUT_PADDING_SECONDS,
        )

    def test_request_text_sets_subprocess_timeout_padding(self) -> None:
        completed_process = mock.Mock(stdout="ok")

        with (
            mock.patch.object(discover_jobs.shutil, "which", return_value="/usr/bin/curl"),
            mock.patch.object(discover_jobs.subprocess, "run", return_value=completed_process) as run_mock,
        ):
            result = discover_jobs.request_text("https://example.com/page", timeout=10)

        self.assertEqual(result, "ok")
        self.assertEqual(
            run_mock.call_args.kwargs["timeout"],
            10 + discover_jobs.SUBPROCESS_TIMEOUT_PADDING_SECONDS,
        )


class USAJobsDiscoveryTests(unittest.TestCase):
    def profile(self) -> dict[str, object]:
        return {
            "title_targets": ["Senior Software Engineer", "IT Specialist"],
            "strong_keywords": ["java", "spring boot", "rest api", "sql"],
            "acceptable_secondary_keywords": ["technical leadership"],
            "exclude_if_primary_focus": [],
        }

    def test_usajobs_skips_cleanly_when_credentials_are_missing(self) -> None:
        errors: list[discover_jobs.DiscoveryError] = []

        with mock.patch.dict(discover_jobs.os.environ, {}, clear=True):
            jobs = discover_jobs.discover_usajobs(
                profile=self.profile(),
                today=discover_jobs.dt.date(2026, 7, 20),
                max_age_days=60,
                errors=errors,
            )

        self.assertEqual(jobs, [])
        self.assertEqual(errors[0].company, "USAJOBS")
        self.assertIn("missing USAJOBS_USER_AGENT or USAJOBS_AUTHORIZATION_KEY", errors[0].message)

    def test_usajobs_api_response_becomes_scored_job_posting(self) -> None:
        payload = {
            "SearchResult": {
                "SearchResultItems": [
                    {
                        "MatchedObjectDescriptor": {
                            "PositionID": "USA-123",
                            "PositionTitle": "IT Specialist (APPSW)",
                            "PositionURI": "https://www.usajobs.gov/job/123",
                            "OrganizationName": "General Services Administration",
                            "PublicationStartDate": "2026-07-18T00:00:00Z",
                            "PositionLocation": [{"LocationName": "Anywhere in the U.S. (remote job)"}],
                            "UserArea": {
                                "Details": {
                                    "JobSummary": "Senior application software role building Java Spring Boot REST API services with SQL.",
                                    "MajorDuties": ["Lead backend integrations and production troubleshooting."],
                                    "Qualifications": "Experience with Java, REST APIs, SQL, and technical leadership.",
                                    "SecurityClearance": "Not Required",
                                    "WhoMayApply": {"Name": "The public"},
                                }
                            },
                        }
                    }
                ]
            }
        }

        with (
            mock.patch.dict(
                discover_jobs.os.environ,
                {
                    "USAJOBS_USER_AGENT": "jobagent-test@example.com",
                    "USAJOBS_AUTHORIZATION_KEY": "secret",
                },
                clear=True,
            ),
            mock.patch.object(discover_jobs, "request_json", return_value=payload) as request_mock,
        ):
            jobs = discover_jobs.discover_usajobs(
                profile=self.profile(),
                today=discover_jobs.dt.date(2026, 7, 20),
                max_age_days=60,
                errors=[],
            )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, "General Services Administration")
        self.assertEqual(jobs[0].job_id, "USA-123")
        self.assertEqual(jobs[0].source, "usajobs")
        self.assertEqual(jobs[0].posted_age_days, 2)
        self.assertIn(jobs[0].decision, {"Apply", "Maybe"})
        self.assertEqual(request_mock.call_args.kwargs["headers"]["User-Agent"], "jobagent-test@example.com")
        self.assertIn("Authorization-Key", request_mock.call_args.kwargs["headers"])


class WorkdayRuntimeControlsTests(unittest.TestCase):
    def test_workday_detail_failures_cannot_keep_live_status(self) -> None:
        url = "https://example.wd1.myworkdayjobs.com/Careers/job/Remote/Engineer_REQ-1"
        failures = [
            discover_jobs.HTTPError(url, code, "failed", None, None)
            for code in (403, 404, 410)
        ] + [discover_jobs.URLError("unreachable"), TimeoutError("timeout")]
        for failure in failures:
            with self.subTest(failure=failure), mock.patch.object(
                discover_jobs, "request_json", side_effect=failure,
            ):
                errors: list[discover_jobs.DiscoveryError] = []
                job = discover_jobs.WorkdayAdapter("https://example.wd1.myworkdayjobs.com/Careers").fetch_detail(discover_jobs.JobPosting(
                    company="ExampleCo", title="Senior Software Engineer", url=url,
                ), self.today, errors)
                self.assertNotEqual("official_live", job.verification_status)
                self.assertTrue(errors)
                discover_jobs.score_job(job, self.profile, 60)
                self.assertEqual("Skip", job.decision)

    def test_workday_empty_and_error_payloads_do_not_verify_a_posting(self) -> None:
        for payload in ({}, {"errorCode": "S22", "httpStatus": 403}, {"jobPostingInfo": None}, {"jobPostingInfo": {}}):
            with self.subTest(payload=payload), mock.patch.object(discover_jobs, "request_json", return_value=payload):
                errors: list[discover_jobs.DiscoveryError] = []
                job = discover_jobs.WorkdayAdapter("https://example.wd1.myworkdayjobs.com/Careers").fetch_detail(discover_jobs.JobPosting(
                    company="ExampleCo", title="Senior Software Engineer",
                    url="https://example.wd1.myworkdayjobs.com/Careers/job/Remote/Engineer_REQ-1",
                ), self.today, errors)
                self.assertNotEqual("official_live", job.verification_status)
                self.assertTrue(errors)

    def test_workday_success_replaces_prior_failure_status(self) -> None:
        with mock.patch.object(discover_jobs, "request_json", return_value={"jobPostingInfo": {
            "title": "Senior Software Engineer", "jobReqId": "REQ-1",
            "jobDescription": "Build Java APIs", "posted": True, "canApply": True,
        }}):
            job = discover_jobs.WorkdayAdapter("https://example.wd1.myworkdayjobs.com/Careers").fetch_detail(discover_jobs.JobPosting(
                company="ExampleCo", title="Senior Software Engineer", verification_status="verification_failed",
                url="https://example.wd1.myworkdayjobs.com/Careers/job/Remote/Engineer_REQ-1",
            ), self.today, [])
        self.assertEqual("official_live", job.verification_status)

    def test_workday_detail_dates_are_read_after_successful_verification(self) -> None:
        adapter = discover_jobs.WorkdayAdapter("https://example.wd1.myworkdayjobs.com/Careers")
        for dates, expected, basis in (({"startDate": "2026-06-22"}, 1, "startDate"),
                                       ({"postedOn": "Posted 2 Days Ago"}, 2, "Posted 2 Days Ago")):
            with self.subTest(dates=dates), mock.patch.object(discover_jobs, "request_json", return_value={"jobPostingInfo": {
                "title": "Engineer", "jobDescription": "Java APIs", **dates,
            }}):
                job = adapter.fetch_detail(discover_jobs.JobPosting(
                    company="ExampleCo", title="Engineer",
                    url="https://example.wd1.myworkdayjobs.com/Careers/job/Engineer_REQ-1",
                ), self.today, [])
                self.assertEqual("official_live", job.verification_status)
                self.assertEqual(expected, job.posted_age_days)
                self.assertIn(basis, job.date_basis)

    def test_workday_invalid_path_and_closed_flags_are_not_live(self) -> None:
        adapter = discover_jobs.WorkdayAdapter("https://example.wd1.myworkdayjobs.com/Careers")
        invalid = adapter.fetch_detail(discover_jobs.JobPosting(
            company="ExampleCo", title="Engineer", url="https://example.com/jobs/1",
        ), self.today, [])
        self.assertEqual("verification_failed", invalid.verification_status)
        for flag in ("posted", "canApply"):
            with self.subTest(flag=flag), mock.patch.object(discover_jobs, "request_json", return_value={"jobPostingInfo": {
                "title": "Engineer", "jobDescription": "Java APIs", flag: False,
            }}):
                job = adapter.fetch_detail(discover_jobs.JobPosting(
                    company="ExampleCo", title="Engineer",
                    url="https://example.wd1.myworkdayjobs.com/Careers/job/Engineer_REQ-1",
                ), self.today, [])
                self.assertEqual("official_unavailable", job.verification_status)

    def test_workday_public_url_cannot_verify_after_detail_failure(self) -> None:
        url = "https://example.wd1.myworkdayjobs.com/Careers/job/Engineer_REQ-1"
        with mock.patch.object(discover_jobs, "request_json", side_effect=discover_jobs.URLError("blocked")):
            job = discover_jobs.job_from_ats_url(
                company=self.company, official_url=url, target_title="Senior Software Engineer",
                hit=discover_jobs.PublicBoardHit(company="ExampleCo", source="manual-url", query="", target_title="Senior Software Engineer", title="Senior Software Engineer", url=url),
                profile=self.profile, today=self.today, max_age_days=60, errors=[],
            )
        self.assertIsNone(job)

    def setUp(self) -> None:
        self.adapter = discover_jobs.WorkdayAdapter(
            "https://example.myworkdayjobs.com/en-US/Careers"
        )
        self.company = {"name": "ExampleCo"}
        self.profile = {
            "title_targets": ["Senior Software Engineer"],
            "strong_keywords": [],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        self.today = discover_jobs.dt.date(2026, 6, 23)
        self.max_age_days = 30

    def test_workday_detail_preserves_additional_locations(self) -> None:
        adapter = discover_jobs.WorkdayAdapter(
            "https://example.myworkdayjobs.com/Careers"
        )
        job = discover_jobs.JobPosting(
            company="Workday",
            title="Principal Software Engineer",
            url=(
                "https://example.myworkdayjobs.com/Careers/job/USA-CA-Pleasanton/"
                "Principal-Software-Engineer_JR-1"
            ),
            location="USA, CA, Pleasanton",
        )
        response = {
            "jobPostingInfo": {
                "title": job.title,
                "jobDescription": "Build distributed systems.",
                "location": "USA, CA, Pleasanton",
                "additionalLocations": ["USA, GA, Atlanta"],
                "remoteType": "Flex",
                "jobReqId": "JR-1",
                "externalUrl": job.url,
            }
        }

        with mock.patch.object(discover_jobs, "request_json", return_value=response):
            detailed = adapter.fetch_detail(job, self.today, [])

        self.assertEqual("USA, CA, Pleasanton; USA, GA, Atlanta", detailed.location)

    def test_workday_stops_after_too_many_nonproductive_searches(self) -> None:
        terms = [
            f"term-{index}"
            for index in range(discover_jobs.MAX_WORKDAY_NON_PRODUCTIVE_SEARCHES_PER_COMPANY + 3)
        ]
        request_count = 0

        def fake_request_json(
            url: str,
            *,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            timeout: int = 12,
        ) -> dict[str, object]:
            nonlocal request_count
            request_count += 1
            return {"jobPostings": []}

        with (
            mock.patch.object(discover_jobs, "build_search_terms", return_value=terms),
            mock.patch.object(discover_jobs, "request_json", side_effect=fake_request_json),
        ):
            jobs = self.adapter.discover(
                self.company,
                self.profile,
                self.today,
                self.max_age_days,
                [],
            )

        self.assertEqual(jobs, [])
        self.assertEqual(
            request_count,
            discover_jobs.MAX_WORKDAY_NON_PRODUCTIVE_SEARCHES_PER_COMPANY,
        )

    def test_workday_stops_after_enough_viable_unique_jobs(self) -> None:
        terms = [f"term-{index}" for index in range(40)]
        request_count = 0

        def fake_request_json(
            url: str,
            *,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            timeout: int = 12,
        ) -> dict[str, object]:
            nonlocal request_count
            index = request_count
            request_count += 1
            return {
                "jobPostings": [
                    {
                        "title": "Senior Software Engineer",
                        "externalPath": f"/job/{index}",
                        "bulletFields": [f"job-{index}"],
                        "postedOn": "Posted today",
                        "locationsText": "Remote - United States",
                    }
                ]
            }

        with (
            mock.patch.object(discover_jobs, "build_search_terms", return_value=terms),
            mock.patch.object(discover_jobs, "request_json", side_effect=fake_request_json),
            mock.patch.object(
                discover_jobs,
                "MAX_WORKDAY_REQUESTS_PER_COMPANY",
                discover_jobs.MAX_WORKDAY_VIABLE_JOBS_PER_COMPANY + 4,
            ),
            mock.patch.object(discover_jobs, "WORKDAY_COMPANY_TIME_BUDGET_SECONDS", 999),
            mock.patch.object(
                discover_jobs.WorkdayAdapter,
                "fetch_detail",
                autospec=True,
                side_effect=lambda _self, job, _today, _errors: job,
            ),
        ):
            jobs = self.adapter.discover(
                self.company,
                self.profile,
                self.today,
                self.max_age_days,
                [],
            )

        self.assertEqual(
            request_count,
            discover_jobs.MAX_WORKDAY_VIABLE_JOBS_PER_COMPANY,
        )
        self.assertEqual(
            len(jobs),
            discover_jobs.MAX_WORKDAY_VIABLE_JOBS_PER_COMPANY,
        )

    def test_workday_stops_at_request_cap_even_when_terms_keep_returning_results(self) -> None:
        terms = [f"term-{index}" for index in range(40)]
        request_count = 0

        def fake_request_json(
            url: str,
            *,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            timeout: int = 12,
        ) -> dict[str, object]:
            nonlocal request_count
            index = request_count
            request_count += 1
            return {
                "jobPostings": [
                    {
                        "title": "Senior Software Engineer",
                        "externalPath": f"/job/{index}",
                        "bulletFields": [f"job-{index}"],
                        "postedOn": "Posted today",
                        "locationsText": "Remote - United States",
                    }
                ]
            }

        with (
            mock.patch.object(discover_jobs, "build_search_terms", return_value=terms),
            mock.patch.object(discover_jobs, "request_json", side_effect=fake_request_json),
            mock.patch.object(
                discover_jobs.WorkdayAdapter,
                "fetch_detail",
                autospec=True,
                side_effect=lambda _self, job, _today, _errors: job,
            ),
        ):
            jobs = self.adapter.discover(
                self.company,
                self.profile,
                self.today,
                self.max_age_days,
                [],
            )

        self.assertEqual(
            request_count,
            discover_jobs.MAX_WORKDAY_REQUESTS_PER_COMPANY,
        )
        self.assertEqual(
            len(jobs),
            discover_jobs.MAX_WORKDAY_REQUESTS_PER_COMPANY,
        )

    def test_workday_stops_after_consecutive_low_value_searches(self) -> None:
        terms = [
            f"term-{index}"
            for index in range(discover_jobs.MAX_WORKDAY_LOW_VALUE_SEARCHES_PER_COMPANY + 3)
        ]
        request_count = 0

        def fake_request_json(
            url: str,
            *,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            timeout: int = 12,
        ) -> dict[str, object]:
            nonlocal request_count
            index = request_count
            request_count += 1
            return {
                "jobPostings": [
                    {
                        "title": "Unrelated Coordinator",
                        "externalPath": f"/job/{index}",
                        "bulletFields": [f"job-{index}"],
                        "postedOn": "Posted 45 days ago",
                        "locationsText": "Remote - United States",
                    }
                ]
            }

        with (
            mock.patch.object(discover_jobs, "build_search_terms", return_value=terms),
            mock.patch.object(discover_jobs, "request_json", side_effect=fake_request_json),
            mock.patch.object(discover_jobs, "MAX_WORKDAY_REQUESTS_PER_COMPANY", 999),
            mock.patch.object(discover_jobs, "WORKDAY_COMPANY_TIME_BUDGET_SECONDS", 999),
        ):
            jobs = self.adapter.discover(
                self.company,
                self.profile,
                self.today,
                self.max_age_days,
                [],
            )

        self.assertEqual(
            request_count,
            discover_jobs.MAX_WORKDAY_LOW_VALUE_SEARCHES_PER_COMPANY,
        )
        self.assertEqual(
            len(jobs),
            discover_jobs.MAX_WORKDAY_LOW_VALUE_SEARCHES_PER_COMPANY,
        )

    def test_workday_stops_when_company_time_budget_is_exhausted(self) -> None:
        terms = [f"term-{index}" for index in range(40)]
        request_count = 0
        budget = float(discover_jobs.WORKDAY_COMPANY_TIME_BUDGET_SECONDS)
        monotonic_values = iter([0.0, 1.0, 2.0, 3.0, budget + 0.1])

        def fake_request_json(
            url: str,
            *,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            timeout: int = 12,
        ) -> dict[str, object]:
            nonlocal request_count
            index = request_count
            request_count += 1
            return {
                "jobPostings": [
                    {
                        "title": "Unrelated Coordinator",
                        "externalPath": f"/job/{index}",
                        "bulletFields": [f"job-{index}"],
                        "postedOn": "Posted 45 days ago",
                        "locationsText": "Remote - United States",
                    }
                ]
            }

        with (
            mock.patch.object(discover_jobs, "build_search_terms", return_value=terms),
            mock.patch.object(discover_jobs, "request_json", side_effect=fake_request_json),
            mock.patch.object(discover_jobs, "MAX_WORKDAY_REQUESTS_PER_COMPANY", 999),
            mock.patch.object(discover_jobs.time, "monotonic", side_effect=lambda: next(monotonic_values)),
        ):
            jobs = self.adapter.discover(
                self.company,
                self.profile,
                self.today,
                self.max_age_days,
                [],
            )

        self.assertEqual(request_count, 1)
        self.assertEqual(len(jobs), 1)

    def test_workday_stops_after_fatal_board_error(self) -> None:
        terms = [f"term-{index}" for index in range(10)]
        request_count = 0
        errors: list[discover_jobs.DiscoveryError] = []

        def fake_request_json(
            url: str,
            *,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            timeout: int = 12,
        ) -> dict[str, object]:
            del url, method, payload, timeout
            nonlocal request_count
            request_count += 1
            return {"errorCode": "S21", "httpStatus": 404, "jobPostings": []}

        with (
            mock.patch.object(discover_jobs, "build_search_terms", return_value=terms),
            mock.patch.object(discover_jobs, "request_json", side_effect=fake_request_json),
            mock.patch.object(discover_jobs, "MAX_WORKDAY_REQUESTS_PER_COMPANY", 999),
        ):
            jobs = self.adapter.discover(
                self.company,
                self.profile,
                self.today,
                self.max_age_days,
                errors,
            )

        self.assertEqual(jobs, [])
        self.assertEqual(request_count, 1)
        self.assertTrue(any("S21 404" in error.message for error in errors))

    def test_workday_paginates_promising_term_when_first_page_is_full(self) -> None:
        requests: list[dict[str, object]] = []
        errors: list[discover_jobs.DiscoveryError] = []

        def fake_request_json(
            url: str,
            *,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            timeout: int = 12,
        ) -> dict[str, object]:
            del url, method, timeout
            assert payload is not None
            requests.append(payload)
            offset = int(payload.get("offset", 0))
            if offset == 0:
                return {
                    "jobPostings": [
                        {
                            "title": "Staff Software Development Engineer - Frontend",
                            "externalPath": "/job/front-end",
                            "bulletFields": ["job-frontend"],
                            "postedOn": "Posted today",
                            "locationsText": "Remote - United States",
                        }
                    ] * 20
                }
            return {
                "jobPostings": [
                    {
                        "title": "Staff Software Development Engineer",
                        "externalPath": "/job/backend",
                        "bulletFields": ["job-backend"],
                        "postedOn": "Posted today",
                        "locationsText": "Remote - United States",
                    }
                ]
            }

        with (
            mock.patch.object(
                discover_jobs,
                "build_search_terms",
                return_value=["Staff Software Development Engineer"],
            ),
            mock.patch.object(discover_jobs, "request_json", side_effect=fake_request_json),
            mock.patch.object(discover_jobs, "MAX_WORKDAY_REQUESTS_PER_COMPANY", 10),
            mock.patch.object(discover_jobs, "WORKDAY_COMPANY_TIME_BUDGET_SECONDS", 999),
            mock.patch.object(
                discover_jobs.WorkdayAdapter,
                "fetch_detail",
                autospec=True,
                side_effect=lambda _self, job, _today, _errors: job,
            ),
        ):
            jobs = self.adapter.discover(
                self.company,
                self.profile,
                self.today,
                self.max_age_days,
                errors,
            )

        self.assertEqual([request["offset"] for request in requests], [0, 20])
        self.assertTrue(any(job.job_id == "job-backend" for job in jobs))


class AtsRegistryAndManualUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = {
            "title_targets": ["Senior Software Engineer"],
            "strong_keywords": ["java", "spring boot"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        self.today = discover_jobs.dt.date(2026, 7, 15)

    def test_load_ats_registry_urls_for_company(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry_path = Path(tmp_dir) / "ats_registry.yml"
            registry_path.write_text(
                """
companies:
  Example Co:
    ats:
      - type: workday
        url: https://example.wd1.myworkdayjobs.com/External
        verified_on: 2026-07-15
""",
                encoding="utf-8",
            )

            registry = discover_jobs.load_ats_registry(registry_path)

        self.assertEqual(
            discover_jobs.ats_registry_urls_for_company(registry, {"name": "Example Co"}),
            ["https://example.wd1.myworkdayjobs.com/External"],
        )

    def test_discover_company_uses_registry_before_scraping_root(self) -> None:
        company = {"name": "Example Co", "careers_url": "https://example.com/careers"}
        registry = {
            "example co": ["https://example.wd1.myworkdayjobs.com/External"],
        }
        expected_job = discover_jobs.JobPosting(company="Example Co", title="Senior Software Engineer", url="https://example/jobs/1")

        with (
            mock.patch.object(discover_jobs.WorkdayAdapter, "discover", return_value=[expected_job]) as discover_mock,
            mock.patch.object(discover_jobs, "discover_ats_links", return_value=[]) as ats_links_mock,
        ):
            jobs = discover_jobs.discover_company(
                company,
                self.profile,
                self.today,
                60,
                [],
                0,
                False,
                ats_registry=registry,
            )

        self.assertEqual(jobs, [expected_job])
        discover_mock.assert_called_once()
        ats_links_mock.assert_not_called()

    def test_workday_sentinel_uses_one_unfiltered_newest_jobs_request(self) -> None:
        company = {"name": "Example Co", "careers_url": "https://example.com/careers"}
        board_url = "https://example.wd1.myworkdayjobs.com/External"
        response = {
            "jobPostings": [
                {
                    "title": "Principal Software Engineer",
                    "externalPath": "/job/Remote/Principal-Software-Engineer_REQ-1",
                    "locationsText": "Remote - United States",
                    "postedOn": "Posted 1 Day Ago",
                    "bulletFields": ["REQ-1"],
                }
            ]
        }

        with mock.patch.object(discover_jobs, "request_json", return_value=response) as request_mock:
            jobs = discover_jobs.discover_ats_sentinel(
                company=company,
                board_url=board_url,
                profile=self.profile,
                today=self.today,
                max_age_days=60,
                errors=[],
            )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "REQ-1")
        request_mock.assert_called_once()
        self.assertEqual(
            request_mock.call_args.kwargs["payload"],
            {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
        )

    def test_sentinel_rejects_adapters_that_require_deep_page_inspection(self) -> None:
        jobs = discover_jobs.discover_ats_sentinel(
            company={"name": "Root Co", "careers_url": "https://example.com/careers"},
            board_url="https://example.com/careers",
            profile=self.profile,
            today=self.today,
            max_age_days=60,
            errors=[],
        )

        self.assertEqual(jobs, [])

    def test_icims_sentinel_enumerates_listing_without_fetching_job_details(self) -> None:
        board_url = "https://careers-example.icims.com/jobs"
        listing = (
            '<a href="https://careers-example.icims.com/jobs/12345/principal-software-engineer/job">'
            "Principal Software Engineer</a>"
        )

        with (
            mock.patch.object(discover_jobs, "request_text", return_value=listing) as request_mock,
            mock.patch.object(discover_jobs, "fetch_job_page_jobs") as detail_mock,
        ):
            jobs = discover_jobs.discover_ats_sentinel(
                company={"name": "Example", "careers_url": board_url},
                board_url=board_url,
                profile=self.profile,
                today=self.today,
                max_age_days=60,
                errors=[],
            )

        request_mock.assert_called_once_with(board_url)
        detail_mock.assert_not_called()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "12345")

    def test_discover_ats_links_resolves_workday_from_search_when_root_lacks_link(self) -> None:
        company = {"name": "Example Co", "careers_url": "https://example.com/careers"}
        errors: list[discover_jobs.DiscoveryError] = []

        with (
            mock.patch.object(discover_jobs, "request_text", return_value="<html>No links</html>"),
            mock.patch.object(
                discover_jobs,
                "search_public_web",
                return_value=[
                    discover_jobs.SearchResult(
                        title="Example Co Careers",
                        url="https://example.wd1.myworkdayjobs.com/External",
                    )
                ],
            ),
            mock.patch.object(discover_jobs, "is_valid_workday_board", return_value=True),
        ):
            urls = discover_jobs.discover_ats_links(company, errors)

        self.assertEqual(urls, ["https://example.wd1.myworkdayjobs.com/External"])
        self.assertEqual(errors, [])

    def test_load_manual_job_urls_verifies_ats_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manual_path = Path(tmp_dir) / "manual_job_urls.yml"
            manual_path.write_text(
                """
jobs:
  - company: Example Co
    url: https://example.wd1.myworkdayjobs.com/External/job/Remote/Senior-Software-Engineer_REQ-1
    added_on: 2026-07-15
""",
                encoding="utf-8",
            )

            with mock.patch.object(
                discover_jobs,
                "job_from_ats_url",
                return_value=discover_jobs.JobPosting(
                    company="Example Co",
                    title="Senior Software Engineer",
                    url="https://example.wd1.myworkdayjobs.com/External/job/Remote/Senior-Software-Engineer_REQ-1",
                    decision="Apply",
                    fit_score=85,
                ),
            ) as verify_mock:
                jobs = discover_jobs.discover_manual_job_urls(
                    manual_path=manual_path,
                    companies=[{"name": "Example Co", "careers_url": "https://example.com/careers"}],
                    profile=self.profile,
                    today=self.today,
                    max_age_days=60,
                    errors=[],
                )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "manual-url->workday")
        verify_mock.assert_called_once()

    def test_verified_manual_metadata_enriches_sparse_verified_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manual_path = Path(tmp_dir) / "manual_job_urls.yml"
            manual_path.write_text(
                """
jobs:
  - company: Example Co
    title: Platform Architect
    url: https://jobs.ashbyhq.com/example/role-1
    source_url: https://www.linkedin.com/jobs/view/123
    verification_status: official_live
    verified_on: 2026-09-09
    posted_on: 2026-09-01
    location: United States / Remote
    work_model: Remote
    job_id: role-1
    description: Java distributed systems Kubernetes Docker APIs technical leadership mentoring
""",
                encoding="utf-8",
            )

            sparse_job = discover_jobs.JobPosting(
                company="Example Co",
                title="Platform Architect",
                url="https://jobs.ashbyhq.com/example/role-1",
                location="US-REMOTE / Remote",
                work_model="Remote",
                description="FullTime Remote",
                decision="Skip",
                fit_score=35,
                source="ashby",
                verification_status="official_live",
            )
            with mock.patch.object(
                discover_jobs,
                "job_from_ats_url",
                return_value=sparse_job,
            ):
                jobs = discover_jobs.discover_manual_job_urls(
                    manual_path=manual_path,
                    companies=[{"name": "Example Co", "careers_url": "https://example.com/careers"}],
                    profile=self.profile,
                    today=discover_jobs.dt.date(2026, 9, 9),
                    max_age_days=60,
                    errors=[],
                )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].description, "Java distributed systems Kubernetes Docker APIs technical leadership mentoring")
        self.assertEqual(jobs[0].location, "United States / Remote")
        self.assertEqual(jobs[0].posted_on, "2026-09-01")
        self.assertEqual(jobs[0].posted_age_days, 8)
        self.assertEqual(jobs[0].source_url, "https://www.linkedin.com/jobs/view/123")
        self.assertTrue(any("strong keywords: java" in reason for reason in jobs[0].fit_reasons))

    def test_verified_manual_metadata_survives_official_site_waf(self) -> None:
        self.assertTrue(
            discover_jobs.looks_like_first_party_listing_url(
                "https://careers.ibm.com/en_US/careers/SearchJobs"
            )
        )
        self.assertTrue(
            discover_jobs.is_first_party_job_detail_url(
                "https://careers.ibm.com/en_US/careers/JobDetail?jobId=119683"
            )
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            manual_path = Path(tmp_dir) / "manual_job_urls.yml"
            manual_path.write_text(
                """
jobs:
  - company: IBM
    title: Senior Java Software Engineer - Legacy Modernization
    url: https://careers.ibm.com/en_US/careers/JobDetail/Senior-Java-Software-Engineer-Legacy-Modernization/119683
    verification_status: official_live
    verified_on: 2026-08-27
    posted_on: 2026-08-04
    location: Georgia, United States
    work_model: Hybrid
    job_id: "119683"
    description: Java Spring Framework SQL JPA CI/CD Git Maven Docker Jenkins TDD legacy modernization
""",
                encoding="utf-8",
            )

            with mock.patch.object(
                discover_jobs,
                "verify_public_board_hit",
                return_value=None,
            ), mock.patch.object(
                discover_jobs,
                "request_text_with_status",
                side_effect=discover_jobs.URLError("site blocked automated recheck"),
            ):
                jobs = discover_jobs.discover_manual_job_urls(
                    manual_path=manual_path,
                    companies=[
                        {
                            "name": "IBM",
                            "careers_url": "https://careers.ibm.com/en_US/careers/SearchJobs",
                        }
                    ],
                    profile=self.profile,
                    today=discover_jobs.dt.date(2026, 8, 27),
                    max_age_days=60,
                    errors=[],
                )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "119683")
        self.assertEqual(jobs[0].posted_age_days, 23)
        self.assertEqual(jobs[0].verification_status, "official_live")
        self.assertEqual(jobs[0].source, "manual-url->verified-metadata")
        self.assertIn("manually surfaced by the candidate", jobs[0].risk_flags)

    def test_live_posting_ignores_unavailable_labels_in_scripts(self) -> None:
        page = '<script>var pageNameMap={"404":"404_page_view","expired":"job has expired"};</script><style>.error404 {color:red}</style><h1>Lead Engineer</h1><p>3+ years of cloud experience</p>'
        self.assertFalse(discover_jobs.page_looks_unavailable(200, page))

    def test_unavailable_http_and_visible_messages_still_block(self) -> None:
        for status, page in ((404, '<h1>Lead Engineer</h1>'), (410, ''), (200, '<h1>Job has expired</h1>'), (200, '<h1>404</h1>')):
            with self.subTest(status=status, page=page):
                self.assertTrue(discover_jobs.page_looks_unavailable(status, page))
        self.assertFalse(discover_jobs.page_looks_unavailable(200, '<h1>Engineer 404123</h1>'))

    def test_verified_manual_metadata_does_not_revive_soft_404_job_page(self) -> None:
        self.assertTrue(discover_jobs.page_looks_unavailable(200, "The page you are looking for doesn't exist."))
        with tempfile.TemporaryDirectory() as tmp_dir:
            manual_path = Path(tmp_dir) / "manual_job_urls.yml"
            manual_path.write_text(
                """
jobs:
  - company: Stripe
    title: Staff Software Engineer, Startup Products
    url: https://stripe.com/careers/listing/staff-software-engineer-startup-products/8146271
    verification_status: official_live
    verified_on: 2026-08-31
    posted_on: 2026-08-24
    location: United States
    work_model: Remote
    job_id: "8146271"
    description: Staff software engineer backend services APIs technical leadership
""",
                encoding="utf-8",
            )

            with (
                mock.patch.object(discover_jobs, "verify_public_board_hit", return_value=None),
                mock.patch.object(
                    discover_jobs,
                    "request_text_with_status",
                    return_value=(
                        200,
                        "<html><h1>Page not found</h1><p>We couldn't find that page.</p></html>",
                        "https://stripe.com/careers/listing/staff-software-engineer-startup-products/8146271",
                    ),
                ),
            ):
                jobs = discover_jobs.discover_manual_job_urls(
                    manual_path=manual_path,
                    companies=[
                        {
                            "name": "Stripe",
                            "careers_url": "https://stripe.com/jobs/search",
                        }
                    ],
                    profile=self.profile,
                    today=discover_jobs.dt.date(2026, 8, 31),
                    max_age_days=60,
                    errors=[],
                )

        self.assertEqual([], jobs)

    def test_workday_shell_cannot_revive_failed_detail_from_manual_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "manual.yml"
            path.write_text("""jobs:
  - company: Example Co
    title: Senior Software Engineer
    url: https://example.wd1.myworkdayjobs.com/Careers/job/Engineer_REQ-1
    verification_status: official_live
    verified_on: 2026-10-14
    location: Remote US
    description: Build Java Spring Boot REST API SQL backend services
""")
            with mock.patch.object(discover_jobs, "job_from_ats_url", return_value=None), mock.patch.object(
                discover_jobs, "request_text_with_status", return_value=(200, "<div id='root'></div>", "https://example.wd1.myworkdayjobs.com/Careers/job/Engineer_REQ-1"),
            ):
                jobs = discover_jobs.discover_manual_job_urls(
                    manual_path=path, companies=[{"name": "Example Co"}], profile=self.profile,
                    today=discover_jobs.dt.date(2026, 10, 14), max_age_days=60, errors=[],
                )
        self.assertEqual(1, len(jobs))
        self.assertEqual("verification_required", jobs[0].verification_status)
        self.assertEqual("Skip", jobs[0].decision)

    def test_verified_manual_metadata_without_posted_date_uses_verification_date(self) -> None:
        entry = {
            "company": "Indeed",
            "title": "Principal Software Engineer V",
            "url": "https://www.indeed.com/viewjob?jk=aa89b4d0151e7b12",
            "verification_status": "official_live",
            "verified_on": "2026-08-31",
            "location": "United States",
            "work_model": "Remote",
            "job_id": "47198",
            "description": "Principal software engineer distributed systems APIs SQL technical leadership",
        }

        job = discover_jobs.job_from_verified_manual_metadata(
            entry=entry,
            company={"name": "Indeed", "careers_url": "https://www.indeed.com/careers"},
            profile=self.profile,
            today=discover_jobs.dt.date(2026, 8, 31),
            max_age_days=60,
        )

        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.posted_on, "2026-08-31")
        self.assertEqual(job.posted_age_days, 0)
        self.assertIn("official posting date unavailable", job.date_basis)

    def test_manual_company_name_takes_precedence_over_generic_ats_domain_matching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manual_path = Path(tmp_dir) / "manual_job_urls.yml"
            manual_path.write_text(
                """
jobs:
  - company: Oracle
    title: Lead Principal Platform Software Engineer
    url: https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/338426
    verification_status: official_live
    verified_on: 2026-08-27
    posted_on: 2026-08-13
    location: United States
    work_model: Remote
    job_id: "338426"
    description: Java OCI distributed systems Kubernetes technical leadership
""",
                encoding="utf-8",
            )

            with mock.patch.object(discover_jobs, "job_from_ats_url", return_value=None):
                jobs = discover_jobs.discover_manual_job_urls(
                    manual_path=manual_path,
                    companies=[
                        {"name": "Salesforce", "careers_url": "https://www.salesforce.com/company/careers/jobs/"},
                        {"name": "Oracle", "careers_url": "https://careers.oracle.com/jobs/"},
                    ],
                    profile=self.profile,
                    today=discover_jobs.dt.date(2026, 8, 27),
                    max_age_days=60,
                    errors=[],
                )

        self.assertEqual(1, len(jobs))
        self.assertEqual("Oracle", jobs[0].company)
        self.assertEqual("338426", jobs[0].job_id)

    def test_manual_company_name_is_preserved_when_company_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manual_path = Path(tmp_dir) / "manual_job_urls.yml"
            manual_path.write_text(
                """
jobs:
  - company: Camunda
    title: Senior Software Engineer, Backend - Distributed Systems
    url: https://jobs.ashbyhq.com/camunda/example-job-id
    verification_status: official_live
    verified_on: 2026-09-04
    location: United States East
    work_model: Remote
    description: Java distributed systems APIs technical leadership
""",
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    discover_jobs,
                    "match_company_for_url",
                    return_value={"name": "Salesforce", "careers_url": "https://example.com"},
                ) as match_mock,
                mock.patch.object(discover_jobs, "job_from_ats_url", return_value=None),
                mock.patch.object(
                    discover_jobs,
                    "request_text_with_status",
                    side_effect=discover_jobs.URLError("site blocked automated recheck"),
                ),
            ):
                jobs = discover_jobs.discover_manual_job_urls(
                    manual_path=manual_path,
                    companies=[{"name": "Salesforce", "careers_url": "https://example.com"}],
                    profile=self.profile,
                    today=discover_jobs.dt.date(2026, 9, 4),
                    max_age_days=60,
                    errors=[],
                )

        match_mock.assert_not_called()
        self.assertEqual(1, len(jobs))
        self.assertEqual("Camunda", jobs[0].company)

    def test_unverified_manual_metadata_is_not_trusted_when_fetch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manual_path = Path(tmp_dir) / "manual_job_urls.yml"
            manual_path.write_text(
                """
jobs:
  - company: Example Co
    title: Principal Software Engineer
    url: https://example.com/jobs/123
    posted_on: 2026-08-20
    description: Java Spring Boot
""",
                encoding="utf-8",
            )

            with mock.patch.object(
                discover_jobs,
                "verify_public_board_hit",
                return_value=None,
            ):
                jobs = discover_jobs.discover_manual_job_urls(
                    manual_path=manual_path,
                    companies=[{"name": "Example Co", "careers_url": "https://example.com/careers"}],
                    profile=self.profile,
                    today=discover_jobs.dt.date(2026, 8, 27),
                    max_age_days=60,
                    errors=[],
                )

        self.assertEqual(jobs, [])


class ManualFindRegressionFixtureTests(unittest.TestCase):
    def test_linkedin_miss_regressions_have_deterministic_intake_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        fixtures = discover_jobs.load_yaml(root / "tests" / "fixtures" / "linkedin_misses.yml")
        registry = discover_jobs.load_ats_registry(root / "ats_registry.yml")
        manual_entries = discover_jobs.load_manual_job_url_entries(root / "manual_job_urls.yml")

        for case in fixtures["ats_cases"]:
            self.assertIn(
                case["expected_ats_url"],
                discover_jobs.ats_registry_urls_for_company(
                    registry,
                    {"name": case["company"]},
                ),
            )

        manual_urls = {entry["url"] for entry in manual_entries}
        self.assertIn(fixtures["manual_case"]["url"], manual_urls)
        self.assertIn(
            "https://relx.wd3.myworkdayjobs.com/en-US/LexisNexisLegal/job/"
            "Consulting-Principal-Software-Engineer_R112606",
            manual_urls,
        )

    def test_recruiter_branded_hit_resolves_canonical_employer(self) -> None:
        hit = discover_jobs.PublicBoardHit(
            company="LexisNexis Risk Solutions",
            source="Recruiter Marketplace",
            query='"LexisNexis" "Consulting Principal Software Engineer"',
            target_title="Consulting Principal Software Engineer",
            title="Consulting Principal Software Engineer | hackajob",
            url=(
                "https://www.hackajob.com/job/cdca8b04-5541-11f1-a7b8-0a05e249917d-"
                "consulting-principal-software-engineer?company=lexisnexis"
            ),
            snippet=(
                "hackajob is collaborating with LexisNexis to hire a Consulting Principal "
                "Software Engineer using Java, Spring Boot, APIs, SQL, and cloud platforms."
            ),
        )
        profile = {
            "title_targets": ["Consulting Principal Software Engineer"],
            "strong_keywords": ["java", "spring boot", "apis", "sql"],
            "acceptable_secondary_keywords": ["cloud"],
        }

        resolution = discover_jobs.resolve_recruiter_branded_hit(
            hit,
            companies=[
                {
                    "name": "LexisNexis Risk Solutions",
                    "careers_url": "https://www.lexisnexis.com/en-us/about-us/careers.page",
                }
            ],
            profile=profile,
        )

        self.assertIsNotNone(resolution)
        assert resolution is not None
        self.assertEqual(resolution.source_company, "hackajob")
        self.assertEqual(resolution.employer_company, "LexisNexis Risk Solutions")
        self.assertEqual(resolution.confidence, "high")
        self.assertGreater(resolution.preliminary_fit_score, 75)

    def test_recruiter_branded_hit_rejects_low_fit_or_unresolved_employer(self) -> None:
        low_fit = discover_jobs.PublicBoardHit(
            company="LexisNexis Risk Solutions",
            source="Recruiter Marketplace",
            query="",
            target_title="Frontend Designer",
            title="Frontend Designer",
            url="https://www.hackajob.com/job/123?company=lexisnexis",
            snippet="React visual design role collaborating with LexisNexis.",
        )

        resolution = discover_jobs.resolve_recruiter_branded_hit(
            low_fit,
            companies=[{"name": "LexisNexis Risk Solutions", "careers_url": ""}],
            profile={
                "title_targets": ["Principal Software Engineer"],
                "strong_keywords": ["java", "spring boot"],
                "acceptable_secondary_keywords": [],
            },
        )

        self.assertIsNone(resolution)

    def test_recruiter_branded_discovery_is_bounded_and_checks_one_official_candidate(self) -> None:
        company = {
            "name": "LexisNexis Risk Solutions",
            "careers_url": "https://www.lexisnexis.com/en-us/about-us/careers.page",
        }
        profile = {
            "title_targets": ["Consulting Principal Software Engineer"],
            "strong_keywords": ["java", "spring boot", "apis", "sql"],
            "acceptable_secondary_keywords": [],
        }
        results = [
            discover_jobs.SearchResult(
                title="Consulting Principal Software Engineer",
                url=f"https://www.hackajob.com/job/{index}?company=lexisnexis",
                snippet=(
                    "hackajob is collaborating with LexisNexis for Java Spring Boot APIs SQL"
                ),
            )
            for index in range(11)
        ]

        with (
            mock.patch.object(
                discover_jobs,
                "build_public_board_searches",
                return_value=[("Consulting Principal Software Engineer", "query")],
            ),
            mock.patch.object(discover_jobs, "search_public_web", return_value=results),
            mock.patch.object(discover_jobs, "verify_public_board_hit", return_value=None) as verify_mock,
        ):
            jobs = discover_jobs.discover_public_board_verified_jobs(
                companies=[company],
                profile=profile,
                today=discover_jobs.dt.date(2026, 8, 7),
                max_age_days=60,
                errors=[],
                pause_seconds=0,
                max_companies=1,
                queries_per_company=1,
                results_per_query=20,
                total_query_limit=5,
                total_hit_limit=20,
                time_budget_seconds=30,
            )

        self.assertEqual(jobs, [])
        self.assertEqual(verify_mock.call_count, discover_jobs.MAX_RECRUITER_BRANDED_HITS_PER_RUN)
        self.assertTrue(
            all(call.kwargs["max_official_candidates"] == 1 for call in verify_mock.call_args_list)
        )

    def test_manual_first_party_url_is_verified_directly(self) -> None:
        hit = discover_jobs.PublicBoardHit(
            company="FullStack",
            source="manual-url",
            query="manual_job_urls.yml",
            target_title="Principal Agentic Engineer",
            title="Principal Agentic Engineer",
            url="https://talent.fullstack.com/jobs/aef7ee91-020a-464e-a374-b51ac9203461",
        )
        expected = discover_jobs.JobPosting(
            company="FullStack",
            title="Principal Agentic Engineer",
            url=hit.url,
        )

        with (
            mock.patch.object(
                discover_jobs,
                "request_text_with_status",
                return_value=(200, "<html>live role</html>", hit.url),
            ),
            mock.patch.object(discover_jobs, "extract_official_candidate_urls", return_value=[]),
            mock.patch.object(discover_jobs, "job_from_official_page", return_value=expected) as page_mock,
            mock.patch.object(discover_jobs, "search_official_urls_for_hit") as search_mock,
        ):
            job = discover_jobs.verify_public_board_hit(
                hit,
                {"name": "FullStack", "careers_url": "https://talent.fullstack.com/jobs"},
                {},
                discover_jobs.dt.date(2026, 8, 7),
                60,
                [],
            )

            self.assertEqual(job, expected)
            page_mock.assert_called_once()
            search_mock.assert_not_called()

    def test_manual_public_board_url_is_not_treated_as_an_official_page(self) -> None:
        hit = discover_jobs.PublicBoardHit(
            company="GlobalLogic",
            source="manual-url",
            query="manual_job_urls.yml",
            target_title="Forward Deployed AI Architect",
            title="Forward Deployed AI Architect",
            url="https://www.linkedin.com/jobs/view/4449757371/",
        )

        with (
            mock.patch.object(
                discover_jobs,
                "request_text_with_status",
                return_value=(200, "<html>LinkedIn listing</html>", hit.url),
            ),
            mock.patch.object(discover_jobs, "extract_official_candidate_urls", return_value=[]),
            mock.patch.object(discover_jobs, "search_official_urls_for_hit", return_value=[]),
            mock.patch.object(discover_jobs, "job_from_official_page") as page_mock,
        ):
            job = discover_jobs.verify_public_board_hit(
                hit,
                {"name": "GlobalLogic", "careers_url": "https://www.globallogic.com/careers/"},
                {},
                discover_jobs.dt.date(2026, 8, 10),
                60,
                [],
            )

        self.assertIsNone(job)
        page_mock.assert_not_called()

    def test_manual_urls_are_classified_for_known_ats_and_official_patterns(self) -> None:
        fixtures = {
            "insight_global": "https://insightglobal.com/jobs/find_a_job/georgia/dunwoody/remote-senior-forward-deployed-engineer/job-543132/",
            "atlassian": "https://www.atlassian.com/company/careers/details/25302",
            "stripe": "https://stripe.com/jobs/listing/staff-software-engineer-stream-compute/7767063",
            "elevance": "https://elevancehealth.wd1.myworkdayjobs.com/ANT/job/GA-ATLANTA-740-W-PEACHTREE-ST-NW/Engineer-Senior_JR194847",
            "penn_mutual": "https://pennmutual.wd1.myworkdayjobs.com/_penn-careers/job/Remote---United-States/Sr-Staff-Architect---Software-Architecture_R-100617",
        }

        self.assertEqual(discover_jobs.extract_insight_global_job_id(fixtures["insight_global"]), "543132")
        self.assertEqual(discover_jobs.extract_atlassian_job_id(fixtures["atlassian"]), "25302")
        self.assertTrue(discover_jobs.looks_like_job_url(fixtures["stripe"]))
        self.assertEqual(
            discover_jobs.extract_workday_config(fixtures["elevance"]),
            ("elevancehealth", "ANT", "https://elevancehealth.wd1.myworkdayjobs.com"),
        )
        self.assertEqual(
            discover_jobs.extract_workday_config(fixtures["penn_mutual"]),
            ("pennmutual", "_penn-careers", "https://pennmutual.wd1.myworkdayjobs.com"),
        )

    def test_manual_official_url_matches_configured_company_domain(self) -> None:
        stripe = {"name": "Stripe", "careers_url": "https://stripe.com/jobs"}

        self.assertTrue(
            discover_jobs.is_company_official_url(
                "https://stripe.com/jobs/listing/staff-software-engineer-stream-compute/7767063",
                stripe,
            )
        )

    def test_ats_url_matches_company_by_ats_identity_not_first_company(self) -> None:
        companies = [
            {"name": "Wrong Co", "careers_url": "https://wrong.example.com/careers"},
            {"name": "Elevance Health", "careers_url": "https://elevancehealth.wd1.myworkdayjobs.com/ANT"},
        ]

        matched = discover_jobs.match_company_for_url(
            "https://elevancehealth.wd1.myworkdayjobs.com/ANT/job/GA-ATLANTA/Engineer-Senior_JR194847",
            companies,
        )

        self.assertIsNotNone(matched)
        self.assertEqual(matched["name"], "Elevance Health")


class AshbyAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company = {"name": "Docker", "careers_url": "https://www.docker.com/careers/"}
        self.profile = {
            "title_targets": ["Senior Software Engineer"],
            "strong_keywords": ["software", "backend", "api"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        self.today = discover_jobs.dt.date(2026, 7, 16)

    def test_extract_ashby_slug_from_board_and_embed_urls(self) -> None:
        self.assertEqual(
            discover_jobs.extract_ashby_slug("https://jobs.ashbyhq.com/docker"),
            "docker",
        )
        self.assertEqual(
            discover_jobs.extract_ashby_slug("https://jobs.ashbyhq.com/docker/embed"),
            "docker",
        )

    def test_ashby_adapter_parses_window_app_data_job_postings(self) -> None:
        page = """
        <script>
        window.__appData = {
          "jobBoard": {
            "jobPostings": [
              {
                "id": "posting-1",
                "title": "Senior Software Engineer, Backend Platform",
                "departmentName": "Engineering",
                "locationName": "United States",
                "workplaceType": "Remote",
                "employmentType": "FullTime",
                "isListed": true,
                "publishedDate": "2026-07-10",
                "updatedAt": "2026-07-11T10:00:00.000Z"
              }
            ]
          }
        };
        </script>
        """

        with mock.patch.object(discover_jobs, "request_text", return_value=page):
            jobs = discover_jobs.AshbyAdapter("https://jobs.ashbyhq.com/docker").discover(
                self.company,
                self.profile,
                self.today,
                60,
                [],
            )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "ashby")
        self.assertEqual(jobs[0].posted_on, "2026-07-10")
        self.assertEqual(jobs[0].url, "https://jobs.ashbyhq.com/docker/posting-1")
        self.assertIn("Engineering", jobs[0].description)


class ADPAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = {
            "title_targets": ["Lead Solutions Architect"],
            "strong_keywords": ["architecture", "APIs", "distributed systems"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        self.today = discover_jobs.dt.date(2026, 8, 10)

    def test_extract_adp_career_site_domain(self) -> None:
        self.assertEqual(
            "inspiracareers",
            discover_jobs.extract_adp_career_site_domain(
                "https://myjobs.adp.com/inspiracareers/cx/job-details?reqId=5001215356506"
            ),
        )
        self.assertIsNone(
            discover_jobs.extract_adp_career_site_domain("https://example.com/careers")
        )

    def test_adp_adapter_uses_public_token_and_parses_listing(self) -> None:
        metadata = {
            "domain": "inspiracareers",
            "clientName": "Inspira Financial",
            "myJobsToken": "short-lived-public-token",
            "properties": {"myadpUrl": "https://my.adp.com"},
        }
        listing = {
            "jobRequisitions": [
                {
                    "reqId": "5001215356506",
                    "clientRequisitionID": "26-1454",
                    "jobTitle": "Lead Solutions Architect (Remote)",
                    "publishedJobTitle": "Lead Solutions Architect (Remote)",
                    "postingDate": None,
                    "jobDescription": "Architecture strategy, APIs, and distributed systems.",
                    "jobQualifications": "12+ years of architecture leadership.",
                    "requisitionLocations": [
                        {
                            "address": {
                                "cityName": "Oak Brook",
                                "countrySubdivisionLevel1": {"longName": "Illinois"},
                                "country": {"longName": "United States"},
                            }
                        }
                    ],
                }
            ]
        }

        with mock.patch.object(
            discover_jobs,
            "request_json",
            side_effect=[metadata, listing],
        ) as request_mock:
            jobs = discover_jobs.ADPAdapter(
                "https://myjobs.adp.com/inspiracareers/"
            ).discover(
                company={"name": "Inspira Financial"},
                profile=self.profile,
                today=self.today,
                max_age_days=60,
                errors=[],
            )

        self.assertEqual(1, len(jobs))
        self.assertEqual("Lead Solutions Architect (Remote)", jobs[0].title)
        self.assertEqual("26-1454", jobs[0].job_id)
        self.assertEqual("Remote", jobs[0].work_model)
        self.assertEqual("official_live", jobs[0].verification_status)
        self.assertIn("reqId=5001215356506", jobs[0].url)
        self.assertIsNone(jobs[0].posted_age_days)
        listing_call = request_mock.call_args_list[1]
        self.assertEqual(
            "short-lived-public-token",
            listing_call.kwargs["headers"]["myJobsToken"],
        )

    def test_adapter_for_url_recognizes_adp(self) -> None:
        adapter = discover_jobs.adapter_for_url(
            "https://myjobs.adp.com/inspiracareers/cx/job-details?reqId=5001215356506"
        )

        self.assertIsInstance(adapter, discover_jobs.ADPAdapter)
        self.assertTrue(
            discover_jobs.is_ats_url(
                "https://myjobs.adp.com/inspiracareers/cx/job-details?reqId=5001215356506"
            )
        )

    def test_adp_identity_matches_only_the_configured_career_site(self) -> None:
        companies = [
            {"name": "Wrong Co", "careers_url": "https://myjobs.adp.com/wrongcareers/"},
            {"name": "Inspira Financial", "careers_url": "https://myjobs.adp.com/inspiracareers/"},
        ]

        matched = discover_jobs.match_company_for_url(
            "https://myjobs.adp.com/inspiracareers/cx/job-details?reqId=5001215356506",
            companies,
        )

        self.assertIsNotNone(matched)
        self.assertEqual("Inspira Financial", matched["name"])
        self.assertIn("adp", discover_jobs.LIGHTWEIGHT_SENTINEL_ADAPTERS)


class FirstPartyListingAdapterTests(unittest.TestCase):
    def test_strategic_oracle_cpq_partner_boards_use_supported_adapters(self) -> None:
        expected = {
            "https://careers.techmahindra.com/currentopportunity.aspx": "firstparty-listing",
            "https://careers.wipro.com/viewalljobs/?locale=en_US": "firstparty-listing",
            "https://careers.dxc.com/": "firstparty-listing",
            "https://www.trinamix.com/careers/": "firstparty-listing",
            "https://jobs.birlasoft.com/go/US/684844/": "firstparty-listing",
            "https://centroid.applytojob.com/apply": "firstparty-listing",
            "https://www.inoapps.com/join-inoapps/": "firstparty-listing",
            "https://argano.com/careers/open-positions.html": "firstparty-listing",
            "https://infovity.com/careers/": "firstparty-listing",
            "https://vigilant-inc.com/careers/": "firstparty-listing",
            "https://www.zensar.com/careers": "firstparty-listing",
            "https://nexinfo.com/careers/": "firstparty-listing",
            "https://www.jadeglobal.com/careers": "firstparty-listing",
            "https://cherryroad.com/who-we-are/careers/": "firstparty-listing",
            "https://terillium.com/careers/": "firstparty-listing",
            "https://speridiantechnologies.applytojob.com/apply": "firstparty-listing",
        }

        for url, adapter_name in expected.items():
            with self.subTest(url=url):
                adapter = discover_jobs.adapter_for_url(url)
                self.assertIsNotNone(adapter)
                self.assertEqual(adapter_name, adapter.name)

    def test_oracle_consulting_boards_use_supported_discovery_adapters(self) -> None:
        expected = {
            "https://www.kpmguscareers.com/job-search/": "firstparty-listing",
            "https://huron.wd1.myworkdayjobs.com/huroncareers": "workday",
            "https://jobs.us.pwc.com/oracle-jobs": "firstparty-listing",
            "https://www.accenture.com/us-en/careers": "firstparty-listing",
            "https://careers.hcltech.com/go/United-States-Experienced/9557855/": "firstparty-listing",
            "https://www.infosys.com/careers/americas/apply.html": "firstparty-listing",
            "https://apply.deloitte.com/en_US/careers": "firstparty-listing",
            "https://careers.ltm.com/viewalljobs/": "firstparty-listing",
            "https://www.capgemini.com/careers/join-capgemini/job-search/": "firstparty-listing",
            "https://external-mythics.icims.com/jobs/search?hashed=-435768531&ss=1": "icims",
            "https://www.logicom.net/careers/head-office/vacancies-list/": "firstparty-listing",
            "https://atawayglobal.catsone.com/careers/97420/": "firstparty-listing",
        }

        for url, adapter_name in expected.items():
            with self.subTest(url=url):
                adapter = discover_jobs.adapter_for_url(url)
                self.assertIsNotNone(adapter)
                self.assertEqual(adapter_name, adapter.name)

    def test_oracle_consulting_job_detail_urls_are_recognized(self) -> None:
        urls = [
            "https://www.kpmguscareers.com/jobdetail/?jobId=134344",
            "https://careers.hcltech.com/job/Subject-Matter-Expert/87475-en_US/",
            "https://careers.ltm.com/job/Atlanta-Oracle-CPQ-Architect-GA/123456/",
            "https://www.logicom.net/careers/head-office/other/cloud-solutions-architect/",
            "https://atawayglobal.catsone.com/careers/97420/jobs/16788418-manager-practice-erp-oracle-cloud-hf",
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(discover_jobs.is_first_party_job_detail_url(url))

    def test_aon_listing_uses_public_jobs_api(self) -> None:
        company = {
            "name": "Aon",
            "careers_url": "https://jobs.aon.com/jobs",
            "title_aliases": ["Principal Software Engineer"],
        }
        profile = {
            "title_targets": ["Principal Software Engineer"],
            "strong_keywords": ["java", "backend", "api"],
            "acceptable_secondary_keywords": ["integration"],
            "exclude_if_primary_focus": [],
        }
        response = {
            "jobs": [
                {
                    "data": {
                        "slug": "12345",
                        "req_id": "12345",
                        "title": "Principal Software Engineer",
                        "description": "Java backend APIs and enterprise integrations.",
                        "full_location": "Atlanta, Georgia, United States",
                        "tags4": ["Hybrid"],
                        "posted_date": "2026-08-27T13:32:00+0000",
                        "meta_data": {
                            "canonical_url": "https://jobs.aon.com/jobs/12345?lang=en-us"
                        },
                    }
                }
            ]
        }

        with mock.patch.object(discover_jobs, "request_json", return_value=response) as request_mock:
            jobs = discover_jobs.FirstPartyListingAdapter(company["careers_url"]).discover(
                company,
                profile,
                discover_jobs.dt.date(2026, 8, 29),
                60,
                [],
            )

        self.assertEqual(1, len(jobs))
        self.assertEqual("12345", jobs[0].job_id)
        self.assertEqual("2026-08-27", jobs[0].posted_on)
        self.assertEqual("Hybrid", jobs[0].work_model)
        self.assertIn("keywords=Principal+Software+Engineer", request_mock.call_args_list[0].args[0])

    def test_oracle_recruiting_listing_uses_public_requisition_api(self) -> None:
        listing_url = (
            "https://egup.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/"
            "en/sites/CX/requisitions/"
        )
        company = {
            "name": "Vertiv",
            "careers_url": listing_url,
            "title_aliases": ["Principal Software Engineer"],
        }
        profile = {
            "title_targets": ["Principal Software Engineer"],
            "strong_keywords": ["java", "backend", "api"],
            "acceptable_secondary_keywords": ["integration"],
            "exclude_if_primary_focus": [],
        }
        response = {
            "items": [
                {
                    "requisitionList": [
                        {
                            "Id": "20279999",
                            "Title": "Principal Software Engineer",
                            "PostedDate": "2026-08-28",
                            "PrimaryLocation": "Atlanta, GA, United States",
                            "WorkplaceType": "Hybrid",
                            "ShortDescriptionStr": "Java backend APIs and enterprise integrations.",
                            "ExternalQualificationsStr": "Senior technical leadership.",
                            "ExternalResponsibilitiesStr": "Own platform architecture.",
                        }
                    ]
                }
            ]
        }

        with mock.patch.object(discover_jobs, "request_json", return_value=response) as request_mock:
            jobs = discover_jobs.FirstPartyListingAdapter(listing_url).discover(
                company,
                profile,
                discover_jobs.dt.date(2026, 8, 29),
                60,
                [],
            )

        self.assertEqual(1, len(jobs))
        self.assertEqual("20279999", jobs[0].job_id)
        self.assertEqual("2026-08-28", jobs[0].posted_on)
        self.assertEqual("Hybrid", jobs[0].work_model)
        self.assertEqual(
            "https://egup.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/20279999",
            jobs[0].url,
        )
        self.assertIn("recruitingCEJobRequisitions", request_mock.call_args_list[0].args[0])
        self.assertIn("keyword%3DPrincipal+Software+Engineer", request_mock.call_args_list[0].args[0])

    def test_oracle_recruiting_hydrates_empty_summary_once_per_requisition(self) -> None:
        listing_url = (
            "https://ebwh.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/"
            "en/sites/CX_1001/requisitions/"
        )
        company = {
            "name": "Macy's",
            "careers_url": listing_url,
            "title_aliases": ["Principal Software Engineer"],
        }
        profile = {
            "title_targets": ["Principal Software Engineer"],
            "strong_keywords": ["java", "backend", "api"],
            "acceptable_secondary_keywords": ["integration"],
            "exclude_if_primary_focus": [],
        }
        summary_response = {
            "items": [
                {
                    "requisitionList": [
                        {
                            "Id": "88267",
                            "Title": "Principal, Engineer - MarTech",
                            "PostedDate": "2026-07-07",
                            "PrimaryLocation": "Johns Creek, GA, United States",
                            "WorkplaceType": "",
                            "ShortDescriptionStr": "",
                            "ExternalQualificationsStr": None,
                            "ExternalResponsibilitiesStr": None,
                        }
                    ]
                }
            ]
        }
        detail_response = {
            "items": [
                {
                    "Id": "88267",
                    "Title": "Principal, Engineer - MarTech",
                    "ExternalPostedStartDate": "2026-07-07T11:53:33+00:00",
                    "PrimaryLocation": "Johns Creek, GA, United States",
                    "WorkplaceType": "On-site",
                    "ExternalDescriptionStr": (
                        "Expert Java architecture with required Python, Adobe MarTech, and GCP."
                    ),
                    "ExternalQualificationsStr": "Eight years of hands-on Java experience.",
                    "ExternalResponsibilitiesStr": "Lead enterprise platform implementation.",
                }
            ]
        }

        def response_for(url: str) -> dict[str, object]:
            if "recruitingCEJobRequisitionDetails" in url:
                return detail_response
            return summary_response

        with mock.patch.object(discover_jobs, "request_json", side_effect=response_for) as request_mock:
            jobs = discover_jobs.FirstPartyListingAdapter(listing_url).discover(
                company,
                profile,
                discover_jobs.dt.date(2026, 9, 7),
                60,
                [],
            )

        self.assertEqual(1, len(jobs))
        self.assertEqual("88267", jobs[0].job_id)
        self.assertEqual("Onsite", jobs[0].work_model)
        self.assertIn("Adobe MarTech", jobs[0].description)
        detail_calls = [
            call.args[0]
            for call in request_mock.call_args_list
            if "recruitingCEJobRequisitionDetails" in call.args[0]
        ]
        self.assertEqual(1, len(detail_calls))
        self.assertIn("Id%3D88267", detail_calls[0])

    def test_salesforce_shared_board_is_supported_as_first_party(self) -> None:
        listing_url = (
            "https://www.salesforce.com/company/careers/jobs/"
            "?search=MuleSoft&country=United%20States%20of%20America&pagesize=20"
        )
        detail_url = (
            "https://www.salesforce.com/company/careers/jobs/JR345537/"
            "software-engineer-mts-automation-mulesoft-agentic-cloud-services/"
        )

        self.assertTrue(discover_jobs.looks_like_first_party_listing_url(listing_url))
        self.assertTrue(discover_jobs.is_first_party_job_detail_url(detail_url))
        self.assertFalse(discover_jobs.looks_like_icims_url(listing_url))
        self.assertEqual(discover_jobs.adapter_for_url(listing_url).name, "firstparty-listing")

    def test_cognizant_is_first_party_and_not_misclassified_as_icims(self) -> None:
        listing_url = "https://careers.cognizant.com/us-en/jobs/"
        detail_url = (
            "https://careers.cognizant.com/us-en/jobs/00070025471/"
            "senior-lead-engineer-java/"
        )

        self.assertTrue(discover_jobs.looks_like_first_party_listing_url(listing_url))
        self.assertTrue(discover_jobs.is_first_party_job_detail_url(detail_url))
        self.assertFalse(discover_jobs.looks_like_icims_url(listing_url))
        self.assertEqual(discover_jobs.adapter_for_url(listing_url).name, "firstparty-listing")

    def test_cognizant_cloudflare_challenge_is_reported_as_an_adapter_error(self) -> None:
        company = {"name": "Cognizant", "careers_url": "https://careers.cognizant.com/us-en/jobs/"}
        errors: list[discover_jobs.DiscoveryError] = []
        challenge_page = "<html><title>Just a moment...</title>Enable JavaScript and cookies to continue</html>"

        with mock.patch.object(discover_jobs, "request_text", return_value=challenge_page):
            jobs = discover_jobs.FirstPartyListingAdapter(company["careers_url"]).discover(
                company,
                {
                    "title_targets": ["Principal Software Engineer"],
                    "strong_keywords": ["java"],
                    "acceptable_secondary_keywords": [],
                    "exclude_if_primary_focus": [],
                },
                discover_jobs.dt.date(2026, 8, 27),
                60,
                errors,
            )

        self.assertEqual(jobs, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].source, "firstparty-listing")
        self.assertIn("access challenge", errors[0].message)

    def test_listing_page_skips_malformed_candidate_urls(self) -> None:
        company = {"name": "Stripe", "careers_url": "https://stripe.com/jobs"}
        profile = {
            "title_targets": ["Staff Software Engineer"],
            "strong_keywords": ["software", "backend"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        listing_page = """
        <a href="https://[invalid/jobs/listing/broken">Broken listing</a>
        <a href="/jobs/listing/staff-software-engineer-stream-compute/7767063">Valid listing</a>
        """
        detail_page = """
        <html><head><title>Staff Software Engineer, Stream Compute | Stripe</title></head>
        <body>Remote in United States. Backend platform engineering.</body></html>
        """

        def fake_request_text(url: str, *, timeout: int = 10) -> str:
            return listing_page if url == "https://stripe.com/jobs/search" else detail_page

        with mock.patch.object(discover_jobs, "request_text", side_effect=fake_request_text):
            jobs = discover_jobs.FirstPartyListingAdapter("https://stripe.com/jobs/search").discover(
                company,
                profile,
                discover_jobs.dt.date(2026, 8, 4),
                60,
                [],
            )

        self.assertEqual(
            ["https://stripe.com/jobs/listing/staff-software-engineer-stream-compute/7767063"],
            [job.url for job in jobs],
        )

    def test_stripe_listing_page_discovers_direct_job_listing_urls(self) -> None:
        company = {"name": "Stripe", "careers_url": "https://stripe.com/jobs"}
        profile = {
            "title_targets": ["Staff Software Engineer"],
            "strong_keywords": ["software", "backend"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        listing_page = """
        <a href="/jobs/listing/staff-software-engineer-stream-compute/7767063">
          Staff Software Engineer, Stream Compute
        </a>
        """
        detail_page = """
        <html>
          <head><title>Staff Software Engineer, Stream Compute | Stripe</title></head>
          <body>Remote in United States. Backend platform engineering.</body>
        </html>
        """

        def fake_request_text(url: str, *, timeout: int = 10) -> str:
            if url == "https://stripe.com/jobs/search":
                return listing_page
            return detail_page

        with mock.patch.object(discover_jobs, "request_text", side_effect=fake_request_text):
            jobs = discover_jobs.FirstPartyListingAdapter("https://stripe.com/jobs/search").discover(
                company,
                profile,
                discover_jobs.dt.date(2026, 7, 16),
                60,
                [],
            )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "firstparty-listing")
        self.assertEqual(
            jobs[0].url,
            "https://stripe.com/jobs/listing/staff-software-engineer-stream-compute/7767063",
        )

    def test_salesforce_listing_discovers_jr_detail_urls(self) -> None:
        listing_url = (
            "https://www.salesforce.com/company/careers/jobs/"
            "?search=Slack&country=United%20States%20of%20America&pagesize=20"
        )
        detail_url = (
            "https://www.salesforce.com/company/careers/jobs/JR341884/"
            "sr-data-engineer-enterprise-slack/"
        )
        listing_page = f'<a href="{detail_url}">Sr. Data Engineer, Enterprise - Slack</a>'
        detail_page = """
        <html><head><title>Sr. Data Engineer, Enterprise - Slack</title></head>
        <body>United States. Software engineering and distributed data systems.</body></html>
        """
        company = {"name": "Slack", "careers_url": listing_url}
        profile = {
            "title_targets": ["Staff Software Engineer"],
            "strong_keywords": ["software", "distributed"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }

        def fake_request_text(url: str, *, timeout: int = 10) -> str:
            return listing_page if url == listing_url else detail_page

        with mock.patch.object(discover_jobs, "request_text", side_effect=fake_request_text):
            jobs = discover_jobs.FirstPartyListingAdapter(listing_url).discover(
                company,
                profile,
                discover_jobs.dt.date(2026, 8, 27),
                60,
                [],
            )

        self.assertEqual([job.url for job in jobs], [detail_url])
        self.assertEqual(jobs[0].job_id, "JR341884")


class IntuitAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company = {
            "name": "Intuit",
            "careers_url": "https://jobs.intuit.com/search-jobs",
            "title_aliases": ["Staff Backend Software Engineer"],
        }
        self.profile = {
            "title_targets": ["Senior Software Engineer"],
            "strong_keywords": ["java", "backend", "api", "software"],
            "acceptable_secondary_keywords": ["technical leadership"],
            "exclude_if_primary_focus": [],
        }
        self.today = discover_jobs.dt.date(2026, 7, 24)

    def test_intuit_adapter_routes_live_evergreen_posting_to_freshness_verification(self) -> None:
        search_page = """
        <a href="/job/atlanta/staff-backend-software-engineer/27595/87369444880">
          Staff Backend Software Engineer
        </a>
        """
        detail_page = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Staff Backend Software Engineer",
          "datePosted": "2025-02-21",
          "identifier": "87369444880",
          "jobLocation": {
            "address": {
              "addressLocality": "Atlanta",
              "addressRegion": "Georgia",
              "addressCountry": "United States"
            }
          },
          "description": "Build scalable Java backend APIs and mentor engineers."
        }
        </script>
        """

        def fake_request_text(url: str, *, timeout: int = 10) -> str:
            del timeout
            if "/search-jobs" in url:
                return search_page
            return detail_page

        with mock.patch.object(discover_jobs, "request_text", side_effect=fake_request_text) as request_mock:
            jobs = discover_jobs.IntuitAdapter(self.company["careers_url"]).discover(
                self.company,
                self.profile,
                self.today,
                60,
                [],
            )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "intuit")
        self.assertEqual(jobs[0].job_id, "87369444880")
        self.assertEqual(jobs[0].posted_on, "2025-02-21")
        self.assertEqual(jobs[0].decision, "Needs Freshness Verification")
        self.assertTrue(any("needs freshness verification" in risk for risk in jobs[0].risk_flags))
        search_urls = [call.args[0] for call in request_mock.call_args_list if "/search-jobs" in call.args[0]]
        self.assertTrue(any("k=Staff+Backend+Software+Engineer" in url for url in search_urls))

    def test_adapter_for_url_recognizes_intuit_search(self) -> None:
        adapter = discover_jobs.adapter_for_url("https://jobs.intuit.com/search-jobs")

        self.assertIsInstance(adapter, discover_jobs.IntuitAdapter)


class HighSignalCompanyConfigTests(unittest.TestCase):
    def test_intuit_and_mastercard_have_supported_discovery_paths(self) -> None:
        companies = discover_jobs.normalize_company_entries(discover_jobs.load_yaml(Path("companies.yml")))
        by_name = {company["name"]: company for company in companies}

        self.assertIn("Intuit", by_name)
        self.assertIn("Mastercard", by_name)
        self.assertIsInstance(
            discover_jobs.adapter_for_url(str(by_name["Intuit"]["careers_url"])),
            discover_jobs.IntuitAdapter,
        )
        self.assertTrue(
            any(
                discover_jobs.extract_workday_config(url)
                for url in by_name["Mastercard"].get("ats_urls", [])
            )
        )

    def test_oracle_cpq_customer_boards_use_supported_discovery_adapters(self) -> None:
        registry = discover_jobs.load_ats_registry(Path("ats_registry.yml"))

        for company_name in ("Vertiv", "DNV", "Aon", "Cloud Software Group"):
            urls = discover_jobs.ats_registry_urls_for_company(registry, {"name": company_name})
            self.assertEqual(1, len(urls), company_name)
            self.assertIsInstance(
                discover_jobs.adapter_for_url(urls[0]),
                discover_jobs.FirstPartyListingAdapter,
                company_name,
            )

    def test_oracle_cpq_customer_job_detail_urls_are_recognized(self) -> None:
        detail_urls = (
            "https://egup.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/12345",
            "https://jobs.dnv.com/job-search/group/software/atlanta/senior-software-engineer/123",
            "https://jobs.aon.com/jobs/12345?lang=en-us",
            "https://careers.cloud.com/jobs/principal-software-engineer-remote-united-states",
        )

        for url in detail_urls:
            self.assertTrue(discover_jobs.is_first_party_job_detail_url(url), url)


class AtsRegistryConfigTests(unittest.TestCase):
    def test_high_value_registry_entries_resolve_to_supported_adapters(self) -> None:
        registry = discover_jobs.load_ats_registry(Path("ats_registry.yml"))
        expected_companies = [
            "Atlassian",
            "Insight Global Staffing",
            "Docker",
            "Stripe",
            "Southern Company",
            "Georgia Power",
            "BlackRock",
        ]

        for company_name in expected_companies:
            urls = discover_jobs.ats_registry_urls_for_company(
                registry,
                {"name": company_name},
            )
            self.assertGreater(len(urls), 0, company_name)
            self.assertTrue(
                any(discover_jobs.adapter_for_url(url) for url in urls),
                company_name,
            )


class JobOverrideTests(unittest.TestCase):
    def test_apply_job_overrides_stops_using_expired_company_skip(self) -> None:
        job = discover_jobs.JobPosting(
            company="Block",
            title="Staff Software Engineer",
            url="https://block.xyz/careers/jobs/123",
            fit_score=92,
            decision="Apply",
            risk_flags=[],
        )
        overrides = [
            {
                "company": "Block",
                "decision": "Skip",
                "fit_score": "0",
                "expires_on": "2026-10-28",
            }
        ]

        discover_jobs.apply_job_overrides(
            [job],
            overrides,
            as_of=discover_jobs.dt.date(2026, 10, 29),
        )

        self.assertEqual("Apply", job.decision)
        self.assertEqual(92, job.fit_score)

    def test_apply_job_overrides_forces_reviewed_stretch_job_to_skip(self) -> None:
        job = discover_jobs.JobPosting(
            company="NCR Voyix",
            title="Senior Software Engineer",
            url="https://ncr.wd1.myworkdayjobs.com/ext_us/job/ATLANTA-GA-USA/Senior-Software-Engineer_R0157400",
            fit_score=98,
            decision="Apply",
            risk_flags=[],
        )
        overrides = [
            {
                "url_contains": "R0157400",
                "decision": "Skip",
                "fit_score": "35",
                "reason": "reviewed stretch: requires 7+ years C++ on Windows",
            }
        ]

        discover_jobs.apply_job_overrides([job], overrides)

        self.assertEqual(job.decision, "Skip")
        self.assertEqual(job.fit_score, 35)
        self.assertIn("reviewed stretch: requires 7+ years C++ on Windows", job.risk_flags)

    def test_apply_job_overrides_supports_title_contains_skip(self) -> None:
        job = discover_jobs.JobPosting(
            company="Hightouch",
            title="Solutions Engineer, Enterprise East (Pre-Sales)",
            url="https://example.com/hightouch/1",
            fit_score=90,
            decision="Apply",
            risk_flags=[],
        )

        discover_jobs.apply_job_overrides(
            [job],
            [{"title_contains": "pre-sales", "decision": "Skip", "fit_score": "0"}],
        )

        self.assertEqual("Skip", job.decision)
        self.assertEqual(0, job.fit_score)


class DiscoveryCachingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company = {"name": "ExampleCo", "careers_url": "https://boards.greenhouse.io/exampleco"}
        self.profile = {
            "title_targets": ["Senior Software Engineer"],
            "strong_keywords": ["java", "spring"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        self.today = discover_jobs.dt.date(2026, 6, 23)
        self.max_age_days = 30

    def test_job_from_ats_url_reuses_cached_adapter_results(self) -> None:
        cached_job = discover_jobs.JobPosting(
            company="ExampleCo",
            title="Senior Software Engineer",
            url="https://boards.greenhouse.io/exampleco/jobs/123",
            location="Remote - United States",
            work_model="Remote",
            posted_on="2026-06-23",
            posted_age_days=0,
            source="greenhouse",
            verification_status="official_live",
            description="Java Spring role",
        )
        adapter = mock.Mock()
        adapter.name = "greenhouse"
        adapter.discover.return_value = [cached_job]
        errors: list[discover_jobs.DiscoveryError] = []
        cache: dict[tuple[str, str], list[discover_jobs.JobPosting]] = {}

        with mock.patch.object(discover_jobs, "adapter_for_url", return_value=adapter):
            first = discover_jobs.job_from_ats_url(
                company=self.company,
                official_url="https://boards.greenhouse.io/exampleco/jobs/123",
                target_title="Senior Software Engineer",
                hit=discover_jobs.PublicBoardHit(
                    company="ExampleCo",
                    source="LinkedIn Jobs",
                    query="query",
                    target_title="Senior Software Engineer",
                    title="Senior Software Engineer",
                    url="https://www.linkedin.com/jobs/view/1",
                ),
                profile=self.profile,
                today=self.today,
                max_age_days=self.max_age_days,
                errors=errors,
                adapter_result_cache=cache,
            )
            second = discover_jobs.job_from_ats_url(
                company=self.company,
                official_url="https://boards.greenhouse.io/exampleco/jobs/99999",
                target_title="Senior Software Engineer",
                hit=discover_jobs.PublicBoardHit(
                    company="ExampleCo",
                    source="Indeed",
                    query="query",
                    target_title="Senior Software Engineer",
                    title="Senior Software Engineer",
                    url="https://www.indeed.com/viewjob?jk=1",
                ),
                profile=self.profile,
                today=self.today,
                max_age_days=self.max_age_days,
                errors=errors,
                adapter_result_cache=cache,
            )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        adapter.discover.assert_called_once()

    def test_job_from_ats_url_prefers_exact_requisition_over_duplicate_title(self) -> None:
        requested_url = "https://jobs.ashbyhq.com/camunda/92ef5a95-5d25-44e6-aa5b-58e8549f8520"
        requested_job = discover_jobs.JobPosting(
            company="Camunda",
            title="Senior Software Engineer, Backend - Distributed Systems",
            url=requested_url,
            job_id="92ef5a95-5d25-44e6-aa5b-58e8549f8520",
            description="Java distributed systems APIs",
        )
        wrong_job = discover_jobs.JobPosting(
            company="Camunda",
            title="Senior Software Engineer, Backend - Distributed Systems",
            url="https://jobs.ashbyhq.com/camunda/af27a3de-d3ba-40dd-976f-9767bdc1e7e8",
            job_id="af27a3de-d3ba-40dd-976f-9767bdc1e7e8",
            description="Go reliability engineering",
        )
        adapter = mock.Mock()
        adapter.name = "ashby"
        adapter.discover.return_value = [wrong_job, requested_job]

        with mock.patch.object(discover_jobs, "adapter_for_url", return_value=adapter):
            result = discover_jobs.job_from_ats_url(
                company={"name": "Camunda", "careers_url": ""},
                official_url=requested_url,
                target_title=requested_job.title,
                hit=discover_jobs.PublicBoardHit(
                    company="Camunda",
                    source="manual-url",
                    query="manual_job_urls.yml",
                    target_title=requested_job.title,
                    title=requested_job.title,
                    url=requested_url,
                ),
                profile=self.profile,
                today=self.today,
                max_age_days=self.max_age_days,
                errors=[],
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(requested_url, result.url)

    def test_discover_company_dedupes_by_ats_identity_not_raw_url(self) -> None:
        company = {"name": "ExampleCo", "careers_url": "https://example.com/careers"}
        profile = {
            "title_targets": ["Senior Software Engineer"],
            "strong_keywords": ["java"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        errors: list[discover_jobs.DiscoveryError] = []
        adapter = mock.Mock()
        adapter.name = "workday"
        adapter.discover.return_value = []

        urls = [
            "https://example.myworkdayjobs.com/en-US/Careers/job/1",
            "https://example.myworkdayjobs.com/en-US/Careers/job/2",
            "https://example.myworkdayjobs.com/en-US/Careers/job/3",
        ]

        def fake_adapter_for_url(url: str, page: str = ""):
            del page
            if "myworkdayjobs.com" in url:
                return adapter
            return None

        with (
            mock.patch.object(discover_jobs, "discover_ats_links", return_value=urls),
            mock.patch.object(discover_jobs, "adapter_for_url", side_effect=fake_adapter_for_url),
        ):
            jobs = discover_jobs.discover_company(
                company,
                profile,
                discover_jobs.dt.date(2026, 6, 25),
                30,
                errors,
                0.0,
                False,
            )

        self.assertEqual(jobs, [])
        adapter.discover.assert_called_once()

    def test_discover_company_uses_explicit_ats_urls_without_scraping_homepage(self) -> None:
        company = {
            "name": "CVS Health",
            "careers_url": "https://jobs.cvshealth.com/",
            "ats_urls": ["https://cvshealth.wd1.myworkdayjobs.com/CVS_Health_Careers"],
        }
        profile = {
            "title_targets": ["Senior Software Engineer"],
            "strong_keywords": ["java"],
            "acceptable_secondary_keywords": [],
            "exclude_if_primary_focus": [],
        }
        errors: list[discover_jobs.DiscoveryError] = []
        adapter = mock.Mock()
        adapter.name = "workday"
        adapter.discover.return_value = []

        def fake_adapter_for_url(url: str, page: str = ""):
            del page
            if "myworkdayjobs.com" in url:
                return adapter
            return None

        with (
            mock.patch.object(discover_jobs, "discover_ats_links") as discover_links_mock,
            mock.patch.object(discover_jobs, "adapter_for_url", side_effect=fake_adapter_for_url),
        ):
            jobs = discover_jobs.discover_company(
                company,
                profile,
                discover_jobs.dt.date(2026, 6, 25),
                30,
                errors,
                0.0,
                False,
            )

        self.assertEqual(jobs, [])
        discover_links_mock.assert_not_called()
        adapter.discover.assert_called_once()


class InsightGlobalDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company = {
            "name": "Insight Global Staffing",
            "careers_url": "https://jobs.insightglobal.com/",
            "official_domains": ["insightglobal.com", "jobs.insightglobal.com"],
            "title_aliases": ["Forward Deployed Engineer"],
        }
        self.profile = {
            "title_targets": ["Senior Software Engineer", "Technical Consultant"],
            "strong_keywords": ["java", "spring boot", "api", "sql", "client engagement"],
            "acceptable_secondary_keywords": ["full stack", "data engineering"],
            "exclude_if_primary_focus": ["production ai/ml engineer"],
            "locations": {"preferred": ["Example City", "Remote US"]},
        }
        self.today = discover_jobs.dt.date(2026, 7, 7)

    def test_insight_global_detail_url_uses_custom_adapter(self) -> None:
        url = "https://jobs.insightglobal.com/find_a_job/georgia/job-543132/"

        adapter = discover_jobs.adapter_for_url(url)

        self.assertIsInstance(adapter, discover_jobs.InsightGlobalAdapter)
        self.assertTrue(discover_jobs.is_ats_url(url))

    def test_insight_global_adapter_parses_detail_page(self) -> None:
        page = """
        <html>
          <head>
            <title>REMOTE Senior Forward Deployed Engineer job in Dunwoody, Georgia | Insight Global Careers</title>
          </head>
          <body>
            <div itemprop="datePosted" class="nowrap">2026-07-06</div>
            <span>Dunwoody, Georgia</span>
            <h2>Job Description</h2>
            <p>Senior Forward Deployed Engineer leading client engagement workstreams.
            Java Spring Boot API SQL backend full stack integration delivery.</p>
            <a href="https://jobs.insightglobal.com/users/jobapplynoaccount.aspx?jobid=543132">Apply</a>
          </body>
        </html>
        """
        errors: list[discover_jobs.DiscoveryError] = []
        adapter = discover_jobs.InsightGlobalAdapter(
            "https://jobs.insightglobal.com/find_a_job/georgia/job-543132/"
        )

        with mock.patch.object(discover_jobs, "request_text", return_value=page):
            jobs = adapter.discover(self.company, self.profile, self.today, 45, errors)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "543132")
        self.assertIn("Senior Forward Deployed Engineer", jobs[0].title)
        self.assertEqual(jobs[0].posted_age_days, 1)
        self.assertFalse(jobs[0].description.startswith("/h2>"))
        self.assertGreaterEqual(jobs[0].fit_score, 50)

    def test_insight_global_adapter_discovers_detail_urls_from_board_page(self) -> None:
        board_page = """
        <html><body>
          <a href="/find_a_job/georgia/job-543132/">REMOTE Senior Forward Deployed Engineer</a>
        </body></html>
        """
        detail_page = """
        <html>
          <head>
            <title>REMOTE Senior Forward Deployed Engineer job in Dunwoody, Georgia | Insight Global Careers</title>
          </head>
          <body>
            <div itemprop="datePosted" class="nowrap">2026-07-06</div>
            <p>Remote client engagement Java Spring Boot API SQL backend delivery.</p>
          </body>
        </html>
        """
        errors: list[discover_jobs.DiscoveryError] = []
        adapter = discover_jobs.InsightGlobalAdapter("https://jobs.insightglobal.com/")

        def fake_request_text(url: str, *, timeout: int = 10) -> str:
            del timeout
            if url == "https://jobs.insightglobal.com/":
                return board_page
            return detail_page

        with mock.patch.object(discover_jobs, "request_text", side_effect=fake_request_text):
            jobs = adapter.discover(self.company, self.profile, self.today, 45, errors)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "543132")

    def test_insight_global_domains_are_official_for_company(self) -> None:
        self.assertTrue(
            discover_jobs.is_company_official_url(
                "https://insightglobal.com/jobs/find_a_job/georgia/dunwoody/remote-senior-forward-deployed-engineer/job-543132/",
                self.company,
            )
        )
        self.assertTrue(
            discover_jobs.is_company_official_url(
                "https://jobs.insightglobal.com/find_a_job/georgia/job-543132/",
                self.company,
            )
        )

    def test_public_search_can_verify_direct_official_insight_global_result(self) -> None:
        page = """
        <html>
          <head>
            <title>REMOTE Senior Forward Deployed Engineer job in Dunwoody, Georgia | Insight Global Careers</title>
          </head>
          <body>
            <div itemprop="datePosted" class="nowrap">2026-07-06</div>
            <p>Dunwoody, Georgia Remote Senior Forward Deployed Engineer.
            Client engagement Java Spring Boot API SQL backend integration delivery.</p>
          </body>
        </html>
        """
        errors: list[discover_jobs.DiscoveryError] = []
        hit = discover_jobs.PublicBoardHit(
            company="Insight Global Staffing",
            source="public-search",
            query='site:jobs.insightglobal.com/find_a_job "Forward Deployed Engineer"',
            target_title="Senior Forward Deployed Engineer",
            title="REMOTE Senior Forward Deployed Engineer job in Dunwoody, Georgia",
            url="https://jobs.insightglobal.com/find_a_job/georgia/job-543132/",
        )

        with mock.patch.object(
            discover_jobs,
            "request_text_with_status",
            return_value=(200, page, hit.url),
        ):
            job = discover_jobs.verify_public_board_hit(
                hit,
                self.company,
                self.profile,
                self.today,
                45,
                errors,
            )

        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.company, "Insight Global Staffing")
        self.assertIn("Forward Deployed Engineer", job.title)

    def test_insight_global_public_searches_include_official_job_board_domains(self) -> None:
        company = {
            **self.company,
            "official_search_domains": [
                "jobs.insightglobal.com/find_a_job",
                "insightglobal.com/jobs/find_a_job",
            ],
        }

        searches = discover_jobs.build_public_board_searches(company, self.profile)
        queries = [query for _title, query in searches]

        self.assertTrue(any("site:jobs.insightglobal.com/find_a_job" in query for query in queries))
        self.assertTrue(any("site:insightglobal.com/jobs/find_a_job" in query for query in queries))

    def test_public_searches_prioritize_bounded_linkedin_title_lanes(self) -> None:
        searches = discover_jobs.build_public_board_searches(self.company, self.profile)
        first_queries = searches[:4]

        self.assertTrue(all("site:linkedin.com/jobs" in query for _title, query in first_queries))
        combined = " ".join(query for _title, query in first_queries)
        for expected in (
            "Staff Software Engineer",
            "Principal Software Engineer",
            "Senior Principal Software Engineer",
            "Staff Java Engineer",
            "Principal Java Engineer",
            "Software Architect",
            "Cloud Architect",
            "Solutions Architect",
            "Forward Deployed Engineer",
            "Technical Product Manager",
            "Example City",
            "Remote US",
        ):
            self.assertIn(expected, combined)

    def test_excluded_company_filter_is_case_insensitive(self) -> None:
        profile = {"excluded_companies": ["Oracle"]}

        self.assertTrue(discover_jobs.is_excluded_company("oracle", profile))
        self.assertFalse(discover_jobs.is_excluded_company("Oracle partner consulting", profile))


class AtlassianDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company = {
            "name": "Atlassian",
            "careers_url": "https://www.atlassian.com/company/careers",
            "official_domains": ["atlassian.com", "careers-americas.icims.com"],
            "official_search_domains": ["atlassian.com/company/careers/details"],
        }
        self.profile = {
            "title_targets": ["Software Engineer", "Senior Software Engineer"],
            "strong_keywords": ["java", "kotlin", "backend", "distributed systems", "api"],
            "acceptable_secondary_keywords": ["kafka", "streaming", "cloud"],
            "exclude_if_primary_focus": ["frontend-heavy"],
        }
        self.today = discover_jobs.dt.date(2026, 7, 7)

    def test_atlassian_detail_url_uses_custom_adapter(self) -> None:
        url = "https://www.atlassian.com/company/careers/details/25302"

        adapter = discover_jobs.adapter_for_url(url)

        self.assertIsInstance(adapter, discover_jobs.AtlassianAdapter)
        self.assertTrue(discover_jobs.is_ats_url(url))

    def test_atlassian_adapter_parses_detail_page_and_icims_apply_link(self) -> None:
        page = """
        <html>
          <head><title>Software Engineer | Atlassian Careers</title></head>
          <body>
            <h1>Software Engineer</h1>
            <a href="https://careers-americas.icims.com/jobs/25302/software-engineer/login">Apply for this job</a>
            <p>Atlassians can choose where they work whether in an office, from home, or a combination.</p>
            <h2>Technical Skills:</h2>
            <ul>
              <li>Strong backend engineering experience with JVM-based languages Java, Kotlin preferred.</li>
              <li>Experience with Kafka and distributed systems.</li>
            </ul>
            <h2>What you'll bring:</h2>
            <p>3-5 years of backend engineering experience with distributed systems and data-heavy platforms.</p>
          </body>
        </html>
        """
        errors: list[discover_jobs.DiscoveryError] = []
        adapter = discover_jobs.AtlassianAdapter(
            "https://www.atlassian.com/company/careers/details/25302"
        )

        with mock.patch.object(discover_jobs, "request_text", return_value=page):
            jobs = adapter.discover(self.company, self.profile, self.today, 45, errors)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "25302")
        self.assertEqual(jobs[0].title, "Software Engineer")
        self.assertEqual(jobs[0].url, "https://www.atlassian.com/company/careers/details/25302")
        self.assertEqual(jobs[0].date_basis, "")
        self.assertIn("Apply URL: https://careers-americas.icims.com/jobs/25302/software-engineer/login", jobs[0].description)
        self.assertGreaterEqual(jobs[0].fit_score, 50)

    def test_atlassian_adapter_discovers_detail_urls_from_careers_page(self) -> None:
        board_page = """
        <html><body>
          <a href="/company/careers/details/25302">Software Engineer</a>
        </body></html>
        """
        detail_page = """
        <html>
          <head><title>Software Engineer | Atlassian Careers</title></head>
          <body>
            <a href="https://careers-americas.icims.com/jobs/25302/software-engineer/login">Apply</a>
            <p>Backend Java Kotlin distributed systems Kafka API cloud role.</p>
          </body>
        </html>
        """
        errors: list[discover_jobs.DiscoveryError] = []
        adapter = discover_jobs.AtlassianAdapter("https://www.atlassian.com/company/careers")

        def fake_request_text(url: str, *, timeout: int = 10) -> str:
            del timeout
            if url == "https://www.atlassian.com/company/careers":
                return board_page
            return detail_page

        with mock.patch.object(discover_jobs, "request_text", side_effect=fake_request_text):
            jobs = adapter.discover(self.company, self.profile, self.today, 45, errors)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "25302")

    def test_atlassian_public_searches_include_details_domain(self) -> None:
        searches = discover_jobs.build_public_board_searches(self.company, self.profile)
        queries = [query for _title, query in searches]

        self.assertTrue(any("site:atlassian.com/company/careers/details" in query for query in queries))


class XPostDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = {
            "title_targets": [
                "Senior Technical Consultant",
                "Solution Architect",
                "Senior Software Engineer",
            ],
            "strong_keywords": ["java", "spring", "api"],
            "acceptable_secondary_keywords": ["sql"],
            "exclude_if_primary_focus": [],
        }
        self.today = discover_jobs.dt.date(2026, 6, 23)
        self.now = discover_jobs.dt.datetime(2026, 6, 23, 12, 0, 0, tzinfo=discover_jobs.dt.timezone.utc)

    def test_build_x_post_searches_prioritizes_target_titles(self) -> None:
        searches = discover_jobs.build_x_post_searches(self.profile)

        self.assertEqual(len(searches), 50)
        first_title, first_query = searches[0]
        self.assertEqual(first_title, '"hiring java"')
        self.assertIn('site:x.com', first_query)
        self.assertIn('"Java"', first_query)
        self.assertIn('"Spring Boot"', first_query)
        self.assertIn('OR', first_query)
        self.assertIn('"Atlanta"', first_query)
        self.assertIn('"Remote"', first_query)
        self.assertTrue(any('"backend"' in query for _title, query in searches))
        self.assertTrue(any('"software engineer"' in query for _title, query in searches))
        self.assertTrue(any('"dm me"' in query for _title, query in searches))
        self.assertTrue(any('recruiter "java" "spring boot"' in query for _title, query in searches))
        self.assertTrue(any('"solution architect" java spring' in query for _title, query in searches))

    def test_build_threads_post_searches_prioritizes_target_titles(self) -> None:
        searches = discover_jobs.build_threads_post_searches(self.profile)

        self.assertEqual(len(searches), 50)
        first_title, first_query = searches[0]
        self.assertEqual(first_title, '"hiring java"')
        self.assertIn('site:threads.net', first_query)
        self.assertIn('site:threads.com', first_query)
        self.assertIn('"Java"', first_query)
        self.assertIn('"Spring Boot"', first_query)
        self.assertIn('OR', first_query)
        self.assertIn('"Atlanta"', first_query)
        self.assertIn('"Remote"', first_query)

    def test_is_threads_post_url_accepts_threads_dot_com(self) -> None:
        self.assertTrue(discover_jobs.is_threads_post_url("https://www.threads.com/@example/post/abc123"))
        self.assertTrue(discover_jobs.is_threads_post_url("https://threads.net/@example/post/abc123"))
        self.assertFalse(discover_jobs.is_threads_post_url("https://www.threads.com/search?q=java"))

    def test_social_post_matches_profile_filters_irrelevant_posts(self) -> None:
        self.assertTrue(
            discover_jobs.social_post_matches_profile(
                post_title="Hiring Senior Java Engineer",
                post_text="We are hiring a Senior Java Engineer with Spring Boot and REST API experience.",
                profile=self.profile,
            )
        )
        self.assertFalse(
            discover_jobs.social_post_matches_profile(
                post_title="Hiring Python Developer Intern",
                post_text="We are hiring a Python developer intern focused on AI automation and WordPress sites.",
                profile=self.profile,
            )
        )

    def test_parse_google_results_extracts_direct_urls(self) -> None:
        page = """
        <html><body>
        <a href="/url?q=https://x.com/example/status/1&sa=U&ved=0"><h3>Hiring Java</h3></a>
        <a href="/url?q=https://www.threads.net/@example/post/abc&sa=U&ved=0"><h3>Spring Boot</h3></a>
        </body></html>
        """

        results = discover_jobs.parse_google_results(page)

        self.assertEqual(
            [result.url for result in results],
            [
                "https://x.com/example/status/1",
                "https://www.threads.net/@example/post/abc",
            ],
        )

    def test_discover_x_posts_collects_posts_and_same_user_follow_up(self) -> None:
        errors: list[discover_jobs.DiscoveryError] = []

        x_results = [
            discover_jobs.SearchResult(
                title="Hiring Java role at ExampleCo",
                url="https://x.com/example/status/1",
            ),
            discover_jobs.SearchResult(
                title="Looking for Spring Boot engineer",
                url="https://x.com/example/status/2",
            ),
        ]

        def fake_request_text_with_status(url: str, *, timeout: int = 10) -> tuple[int, str, str]:
            if url.endswith("/1"):
                return (
                    200,
                    '<html><time datetime="2026-06-23T10:00:00Z"></time>'
                    '<meta property="og:description" content="We are hiring Java Spring Boot engineers in Atlanta." />'
                    ' https://x.com/example/status/1 more text https://x.com/example/status/99 '
                    ' Follow-up from same user: Team is scaling quickly. '
                    "</html>",
                    url,
                )
            if url.endswith("/2"):
                return (
                    200,
                    '<html><time datetime="2026-06-23T11:00:00Z"></time>'
                    '<meta property="og:description" content="Looking for a Spring Boot engineer. Send me your resume." />'
                    "</html>",
                    url,
                )
            raise AssertionError(f"Unexpected URL: {url}")

        with (
            mock.patch.object(
                discover_jobs,
                "build_x_post_searches",
                return_value=[('"we\'re hiring"', "query")],
            ),
            mock.patch.object(discover_jobs, "search_social_web", return_value=x_results),
            mock.patch.object(discover_jobs, "request_text_with_status", side_effect=fake_request_text_with_status),
        ):
            leads, query_outcomes = discover_jobs.discover_x_posts(
                companies=[],
                profile=self.profile,
                today=self.today,
                now=self.now,
                max_age_days=30,
                errors=errors,
                results_per_query=4,
                total_hit_limit=10,
                time_budget_seconds=60,
            )

        self.assertEqual(len(leads), 2)
        self.assertEqual(leads[0].author_handle, "@example")
        self.assertEqual(leads[0].posted_on, "2026-06-23")
        self.assertIn("java spring boot", leads[0].post_text.lower())
        self.assertIn("team is scaling quickly", leads[0].same_user_follow_up.lower())
        self.assertIn("no same-user follow-up found", leads[1].same_user_follow_up.lower())
        self.assertEqual(len(query_outcomes), 1)
        self.assertEqual(query_outcomes[0].results_found, 2)

    def test_discover_x_posts_skips_posts_older_than_24_hours(self) -> None:
        errors: list[discover_jobs.DiscoveryError] = []

        with (
            mock.patch.object(
                discover_jobs,
                "build_x_post_searches",
                return_value=[("Senior Software Engineer", "query")],
            ),
            mock.patch.object(
                discover_jobs,
                "search_social_web",
                return_value=[
                    discover_jobs.SearchResult(
                        title="Old hiring post",
                        url="https://x.com/example/status/3",
                    )
                ],
            ),
            mock.patch.object(
                discover_jobs,
                "request_text_with_status",
                return_value=(
                    200,
                    '<html><time datetime="2026-06-21T09:00:00Z"></time></html>',
                    "https://x.com/example/status/3",
                ),
            ),
        ):
            leads, query_outcomes = discover_jobs.discover_x_posts(
                companies=[],
                profile=self.profile,
                today=self.today,
                now=self.now,
                max_age_days=30,
                errors=errors,
                results_per_query=4,
                total_hit_limit=10,
                time_budget_seconds=60,
            )

        self.assertEqual(leads, [])
        self.assertEqual(query_outcomes[0].results_found, 1)
        self.assertEqual(query_outcomes[0].leads_collected, 0)

    def test_discover_x_posts_stops_after_consecutive_query_failures(self) -> None:
        errors: list[discover_jobs.DiscoveryError] = []
        searches = [("q1", "query-1"), ("q2", "query-2"), ("q3", "query-3"), ("q4", "query-4")]

        def fake_search_public_web(
            query: str,
            error_list: list[discover_jobs.DiscoveryError],
            company_name: str,
            timeout: int = 6,
        ) -> list[discover_jobs.SearchResult]:
            del company_name, timeout
            error_list.append(discover_jobs.DiscoveryError("X.com", "public-search", f"{query}: timeout"))
            return []

        with (
            mock.patch.object(discover_jobs, "MAX_SOCIAL_CONSECUTIVE_QUERY_FAILURES", 2),
            mock.patch.object(discover_jobs, "MAX_SOCIAL_TOTAL_QUERY_FAILURES", 10),
            mock.patch.object(discover_jobs, "build_x_post_searches", return_value=searches),
            mock.patch.object(discover_jobs, "search_social_web", side_effect=fake_search_public_web),
        ):
            leads, query_outcomes = discover_jobs.discover_x_posts(
                companies=[],
                profile=self.profile,
                today=self.today,
                now=self.now,
                max_age_days=30,
                errors=errors,
                results_per_query=2,
                total_hit_limit=5,
                time_budget_seconds=300,
            )

        self.assertEqual(leads, [])
        self.assertEqual(len(query_outcomes), 2)
        self.assertTrue(any(error.source == "fast-fail" for error in errors))

    def test_discover_threads_posts_stops_after_total_query_failures(self) -> None:
        errors: list[discover_jobs.DiscoveryError] = []
        searches = [("q1", "query-1"), ("q2", "query-2"), ("q3", "query-3"), ("q4", "query-4")]

        def fake_search_public_web(
            query: str,
            error_list: list[discover_jobs.DiscoveryError],
            company_name: str,
            timeout: int = 6,
        ) -> list[discover_jobs.SearchResult]:
            del company_name, timeout
            error_list.append(discover_jobs.DiscoveryError("Threads", "public-search", f"{query}: timeout"))
            return []

        with (
            mock.patch.object(discover_jobs, "MAX_SOCIAL_CONSECUTIVE_QUERY_FAILURES", 99),
            mock.patch.object(discover_jobs, "MAX_SOCIAL_TOTAL_QUERY_FAILURES", 3),
            mock.patch.object(discover_jobs, "build_threads_post_searches", return_value=searches),
            mock.patch.object(discover_jobs, "search_social_web", side_effect=fake_search_public_web),
        ):
            leads, query_outcomes = discover_jobs.discover_threads_posts(
                companies=[],
                profile=self.profile,
                today=self.today,
                now=self.now,
                max_age_days=30,
                errors=errors,
                results_per_query=2,
                total_hit_limit=5,
                time_budget_seconds=300,
            )

        self.assertEqual(leads, [])
        self.assertEqual(len(query_outcomes), 3)
        self.assertTrue(any(error.source == "fast-fail" for error in errors))

    def test_discover_threads_posts_collects_posts_and_no_result_queries(self) -> None:
        errors: list[discover_jobs.DiscoveryError] = []

        threads_results = [
            discover_jobs.SearchResult(
                title="Hiring Solution Architect at ExampleCo",
                url="https://www.threads.net/@example/post/abc123",
            ),
        ]

        def fake_request_text_with_status(url: str, *, timeout: int = 10) -> tuple[int, str, str]:
            if url.endswith("abc123"):
                return (
                    200,
                    '<html><time datetime="2026-06-23T08:00:00Z"></time>'
                    '<meta property="og:description" content="Hiring Solution Architect with Java and Spring Boot background." />'
                    "</html>",
                    url,
                )
            raise AssertionError(f"Unexpected URL: {url}")

        with (
            mock.patch.object(
                discover_jobs,
                "build_threads_post_searches",
                return_value=[("query-one", "query-one"), ("query-two", "query-two")],
            ),
            mock.patch.object(discover_jobs, "search_social_web", side_effect=[threads_results, []]),
            mock.patch.object(discover_jobs, "request_text_with_status", side_effect=fake_request_text_with_status),
        ):
            leads, query_outcomes = discover_jobs.discover_threads_posts(
                companies=[],
                profile=self.profile,
                today=self.today,
                now=self.now,
                max_age_days=30,
                errors=errors,
                results_per_query=4,
                total_hit_limit=10,
                time_budget_seconds=60,
            )

        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].post_url, "https://www.threads.net/@example/post/abc123")
        self.assertEqual(leads[0].posted_on, "2026-06-23")
        self.assertIn("solution architect", leads[0].post_text.lower())
        self.assertEqual(len(query_outcomes), 2)
        self.assertEqual(query_outcomes[1].results_found, 0)

    def test_discover_threads_posts_skips_recent_but_irrelevant_posts(self) -> None:
        errors: list[discover_jobs.DiscoveryError] = []

        threads_results = [
            discover_jobs.SearchResult(
                title="Hiring Python Developer Intern",
                url="https://www.threads.com/@example/post/def456",
            ),
        ]

        def fake_request_text_with_status(url: str, *, timeout: int = 10) -> tuple[int, str, str]:
            if url.endswith("def456"):
                return (
                    200,
                    '<html><time datetime="2026-06-23T08:00:00Z"></time>'
                    '<meta property="og:description" content="We are hiring a Python developer intern for WordPress and Shopify work." />'
                    "</html>",
                    url,
                )
            raise AssertionError(f"Unexpected URL: {url}")

        with (
            mock.patch.object(
                discover_jobs,
                "build_threads_post_searches",
                return_value=[("query-one", "query-one")],
            ),
            mock.patch.object(discover_jobs, "search_social_web", return_value=threads_results),
            mock.patch.object(discover_jobs, "request_text_with_status", side_effect=fake_request_text_with_status),
        ):
            leads, query_outcomes = discover_jobs.discover_threads_posts(
                companies=[],
                profile=self.profile,
                today=self.today,
                now=self.now,
                max_age_days=30,
                errors=errors,
                results_per_query=4,
                total_hit_limit=10,
                time_budget_seconds=60,
            )

        self.assertEqual(leads, [])
        self.assertEqual(len(query_outcomes), 1)
        self.assertEqual(query_outcomes[0].results_found, 1)
        self.assertEqual(query_outcomes[0].leads_collected, 0)

    def test_discover_x_posts_honors_max_query_cap(self) -> None:
        errors: list[discover_jobs.DiscoveryError] = []
        searches = [(f"title-{index}", f"query-{index}") for index in range(10)]
        search_calls: list[str] = []

        def fake_search_social_web(
            query: str,
            error_list: list[discover_jobs.DiscoveryError],
            source_name: str,
            timeout: int = 6,
        ) -> list[discover_jobs.SearchResult]:
            del error_list, source_name, timeout
            search_calls.append(query)
            return []

        with (
            mock.patch.object(discover_jobs, "build_x_post_searches", return_value=searches),
            mock.patch.object(discover_jobs, "search_social_web", side_effect=fake_search_social_web),
            mock.patch.object(discover_jobs, "MAX_X_POST_SEARCH_QUERIES", 3),
        ):
            leads, query_outcomes = discover_jobs.discover_x_posts(
                companies=[],
                profile=self.profile,
                today=self.today,
                now=self.now,
                max_age_days=30,
                errors=errors,
                results_per_query=2,
                total_hit_limit=5,
                time_budget_seconds=300,
            )

        self.assertEqual(leads, [])
        self.assertEqual(len(query_outcomes), 3)
        self.assertEqual(search_calls, ["query-0", "query-1", "query-2"])

    def test_compact_discovered_jobs_payload_truncates_skip_descriptions(self) -> None:
        apply_job = discover_jobs.JobPosting(
            company="ExampleCo",
            title="Senior Software Engineer",
            url="https://example.com/jobs/1",
            description="A" * 5000,
            decision="Apply",
            source="workday",
        )
        skip_job = discover_jobs.JobPosting(
            company="ExampleCo",
            title="Unrelated Coordinator",
            url="https://example.com/jobs/2",
            description="B" * 5000,
            decision="Skip",
            source="workday",
        )

        payload = discover_jobs.build_discovered_jobs_payload(
            jobs=[apply_job, skip_job],
            errors=[],
            generated_on="2026-06-25",
            max_age_days=30,
        )

        self.assertEqual(len(payload["jobs"]), 2)
        self.assertEqual(apply_job.description, payload["jobs"][0]["description"])
        self.assertGreater(len(payload["jobs"][0]["description"]), len(payload["jobs"][1]["description"]))
        self.assertLess(len(payload["jobs"][1]["description"]), 5000)

    def test_apply_description_preserves_middle_and_trailing_qualifications(self) -> None:
        description = (
            "Position Purpose: Lead enterprise platform architecture. "
            + ("delivery context " * 300)
            + "What Will Our Ideal Candidate Have? 5+ years Guidewire BillingCenter experience. "
            + ("benefits context " * 300)
            + "Minimum Qualifications: Java, Spring Boot, REST APIs, and SQL are required."
        )
        job = discover_jobs.JobPosting(
            company="ExampleCo",
            title="Principal Software Engineer",
            url="https://example.com/jobs/compact",
            description=description,
            decision="Apply",
            source="workday",
        )

        payload = discover_jobs.build_discovered_jobs_payload(
            jobs=[job],
            errors=[],
            generated_on="2026-08-25",
            max_age_days=60,
        )

        compacted = payload["jobs"][0]["description"]
        self.assertEqual(description, compacted)
        self.assertTrue(compacted.startswith("Position Purpose:"))
        self.assertNotIn("[middle omitted]", compacted)
        self.assertIn("5+ years Guidewire BillingCenter experience", compacted)
        self.assertIn("Minimum Qualifications: Java, Spring Boot, REST APIs, and SQL are required.", compacted)

    def test_freshness_verification_preserves_complete_requirements(self) -> None:
        description = "Java platform engineering " * 300 + "Required specialist-platform expertise."
        job = discover_jobs.JobPosting(
            company="ExampleCo", title="Principal Engineer", url="https://example.com/jobs/freshness",
            description=description, decision=discover_jobs.NEEDS_FRESHNESS_VERIFICATION_DECISION,
        )
        payload = discover_jobs.build_discovered_jobs_payload(
            jobs=[job], errors=[], generated_on="2026-10-14", max_age_days=60,
        )
        self.assertEqual(description, payload["jobs"][0]["description"])

    def test_write_x_post_summary_outputs_verified_and_manual_sections(self) -> None:
        manual_lead = discover_jobs.XPostLead(
            post_url="https://x.com/example/status/2",
            post_title="Looking for an integration consultant",
            author_handle="@example",
            post_text="Looking for an integration consultant in Atlanta. DM me.",
            posted_on="2026-06-23",
            posted_age_hours=1,
            query='site:x.com "Integration Consultant"',
            same_user_follow_up="No same-user follow-up found in first fetched page.",
            external_job_urls=[],
        )
        query_outcome = discover_jobs.SocialQueryOutcome(
            query='site:x.com "Integration Consultant"',
            results_found=1,
            leads_collected=1,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "2026-06-23-x-job-posts.md"
            discover_jobs.write_x_post_summary(
                output_path,
                [manual_lead],
                [query_outcome],
                self.today,
            )

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("# X.com Job Leads - 2026-06-23", content)
        self.assertIn("## Search Coverage", content)
        self.assertIn("## X Posts to Review", content)
        self.assertIn("@example", content)
        self.assertIn("https://x.com/example/status/2", content)
        self.assertIn("No same-user follow-up found", content)
        self.assertIn("https://x.com/search?q=", content)

    def test_write_threads_post_summary_outputs_verified_and_manual_sections(self) -> None:
        manual_lead = discover_jobs.XPostLead(
            post_url="https://www.threads.net/@example/post/def456",
            post_title="Need a senior technical consultant",
            author_handle="@example",
            post_text="Need a senior technical consultant in Atlanta. Reply here.",
            posted_on="2026-06-23",
            posted_age_hours=1,
            query='site:threads.net "Senior Technical Consultant"',
            same_user_follow_up="More details from same user.",
            external_job_urls=[],
        )
        query_outcome = discover_jobs.SocialQueryOutcome(
            query='site:threads.net "Senior Technical Consultant"',
            results_found=0,
            leads_collected=0,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "2026-06-23-threads-job-posts.md"
            discover_jobs.write_threads_post_summary(
                output_path,
                [manual_lead],
                [query_outcome],
                self.today,
            )

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("# Threads Job Leads - 2026-06-23", content)
        self.assertIn("## Search Coverage", content)
        self.assertIn("## Threads Posts to Review", content)
        self.assertIn("No results returned", content)
        self.assertIn("https://www.threads.net/@example/post/def456", content)
        self.assertIn("https://www.threads.com/search?q=", content)


if __name__ == "__main__":
    unittest.main()
