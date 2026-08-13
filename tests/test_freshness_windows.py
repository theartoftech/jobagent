from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


DISCOVER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "discover_jobs.py"
DISCOVER_SPEC = importlib.util.spec_from_file_location("discover_jobs_freshness", DISCOVER_PATH)
if DISCOVER_SPEC is None or DISCOVER_SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {DISCOVER_PATH}")

discover_jobs = importlib.util.module_from_spec(DISCOVER_SPEC)
sys.modules[DISCOVER_SPEC.name] = discover_jobs
DISCOVER_SPEC.loader.exec_module(discover_jobs)

QUEUE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_job_inspection_queue.py"
QUEUE_SPEC = importlib.util.spec_from_file_location("build_job_inspection_queue_freshness", QUEUE_PATH)
if QUEUE_SPEC is None or QUEUE_SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {QUEUE_PATH}")

build_job_inspection_queue = importlib.util.module_from_spec(QUEUE_SPEC)
sys.modules[QUEUE_SPEC.name] = build_job_inspection_queue
QUEUE_SPEC.loader.exec_module(build_job_inspection_queue)


PROFILE = {
    "title_targets": ["Senior Software Engineer"],
    "strong_keywords": ["java", "spring boot", "sql"],
    "acceptable_secondary_keywords": [],
    "exclude_if_primary_focus": [],
}


class FreshnessWindowTests(unittest.TestCase):
    def make_job(self, *, posted_age_days: int | None) -> discover_jobs.JobPosting:
        return discover_jobs.JobPosting(
            company="Example Co",
            title="Senior Software Engineer",
            url="https://example.com/jobs/1",
            location="Remote - United States",
            work_model="Remote",
            posted_age_days=posted_age_days,
            verification_status="official_live",
            description="Java Spring Boot SQL backend services",
        )

    def test_score_job_uses_7_day_bonus_window(self) -> None:
        job = self.make_job(posted_age_days=7)

        discover_jobs.score_job(job, PROFILE, 60)

        self.assertIn("posted within 7 days", job.fit_reasons)

    def test_score_job_uses_21_day_bonus_window(self) -> None:
        job = self.make_job(posted_age_days=21)

        discover_jobs.score_job(job, PROFILE, 60)

        self.assertIn("posted within 21 days", job.fit_reasons)

    def test_score_job_uses_60_day_bonus_window(self) -> None:
        job = self.make_job(posted_age_days=60)

        discover_jobs.score_job(job, PROFILE, 60)

        self.assertIn("posted within 60 days", job.fit_reasons)

    def test_score_job_treats_unknown_date_as_freshest_bucket(self) -> None:
        job = self.make_job(posted_age_days=None)

        discover_jobs.score_job(job, PROFILE, 60)

        self.assertIn("posting date unavailable; treated as freshest bucket", job.fit_reasons)
        self.assertNotIn("posting date unknown", job.risk_flags)

    def test_public_board_freshness_does_not_skip_unknown_date_anymore(self) -> None:
        job = self.make_job(posted_age_days=None)
        job.decision = "Apply"

        discover_jobs.enforce_public_board_freshness(job, 60)

        self.assertEqual("Apply", job.decision)
        self.assertNotIn(
            "official posting date unknown; public-board freshness is not enough",
            job.risk_flags,
        )

    def test_score_job_routes_live_old_posting_to_freshness_verification(self) -> None:
        job = self.make_job(posted_age_days=61)

        discover_jobs.score_job(job, PROFILE, 60)

        self.assertEqual("Needs Freshness Verification", job.decision)
        self.assertIn(
            "official posting is live but older than 60 days; needs freshness verification",
            job.risk_flags,
        )
        self.assertGreaterEqual(job.fit_score, 50)

    def test_score_job_rejects_old_posting_without_live_official_verification(self) -> None:
        job = self.make_job(posted_age_days=61)
        job.verification_status = "official page unavailable"

        discover_jobs.score_job(job, PROFILE, 60)

        self.assertEqual("Skip", job.decision)
        self.assertIn("older than 60 days", job.risk_flags)

    def test_freshness_label_uses_new_windows(self) -> None:
        self.assertEqual("Posted within 7 days", build_job_inspection_queue.freshness_label(7))
        self.assertEqual("Posted within 21 days", build_job_inspection_queue.freshness_label(21))
        self.assertEqual("Posted within 60 days", build_job_inspection_queue.freshness_label(60))


if __name__ == "__main__":
    unittest.main()
