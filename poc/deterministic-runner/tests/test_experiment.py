from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from experiment import (  # noqa: E402
    ParityError,
    build_replay_stages,
    compare_workflow_reviews,
    find_previous_review,
)


def write_review(path: Path, decisions: list[tuple[str, str, str]]) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_on": "2026-10-07",
                "reviews": [
                    {
                        "company": company,
                        "title": title,
                        "url": url,
                        "workflow_decision": decision,
                        "should_generate_resume": decision == "STRONG APPLY",
                        "already_applied": False,
                    }
                    for company, title, url, decision in (
                        (company, title, f"https://example.com/{index}", decision)
                        for index, (company, title, decision) in enumerate(decisions)
                    )
                ],
            }
        )
    )


class ParityTests(unittest.TestCase):
    def test_exact_reviews_produce_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            rows = [("A", "Principal Engineer", "STRONG APPLY"), ("B", "Manager", "SKIP")]
            write_review(baseline, rows)
            write_review(candidate, rows)

            metrics = compare_workflow_reviews(baseline, candidate)

            self.assertEqual(2, metrics.review_count)
            self.assertEqual({"SKIP": 1, "STRONG APPLY": 1}, metrics.decision_counts)
            self.assertEqual(1, metrics.resume_ready_count)

    def test_changed_decision_fails_with_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            write_review(baseline, [("A", "Principal Engineer", "STRONG APPLY")])
            write_review(candidate, [("A", "Principal Engineer", "SKIP")])

            with self.assertRaisesRegex(ParityError, "A.*Principal Engineer"):
                compare_workflow_reviews(baseline, candidate)

    def test_missing_identity_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            write_review(baseline, [("A", "Principal Engineer", "STRONG APPLY")])
            write_review(candidate, [("B", "Principal Engineer", "STRONG APPLY")])
            with self.assertRaisesRegex(ParityError, "identities differ"):
                compare_workflow_reviews(baseline, candidate)

    def test_changed_resume_authorization_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            rows = [("A", "Principal Engineer", "STRONG APPLY")]
            write_review(baseline, rows)
            write_review(candidate, rows)
            value = json.loads(candidate.read_text())
            value["reviews"][0]["should_generate_resume"] = False
            candidate.write_text(json.dumps(value))
            with self.assertRaisesRegex(ParityError, "Resume authorization mismatch"):
                compare_workflow_reviews(baseline, candidate)

    def test_rejects_invalid_review_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            baseline.write_text("[]")
            candidate.write_text("{}")
            with self.assertRaisesRegex(ParityError, "object"):
                compare_workflow_reviews(baseline, candidate)


class ReplayPlanTests(unittest.TestCase):
    def test_finds_latest_previous_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "2026-10-05-workflow-review.json").write_text("{}")
            expected = root / "2026-10-06-workflow-review.json"
            expected.write_text("{}")
            (root / "2026-10-07-workflow-review.json").write_text("{}")
            self.assertEqual(expected, find_previous_review(root, "2026-10-07"))

    def test_missing_previous_review_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "previous workflow review"):
                find_previous_review(Path(directory), "2026-10-07")

    def test_replay_plan_is_serial_and_has_expected_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            work = root / "work"
            repo.mkdir()
            work.mkdir()
            python = root / "python"
            stages = build_replay_stages(
                repo_root=repo,
                work_dir=work,
                source_output_dir=repo / "outputs",
                run_date="2026-10-07",
                python_executable=python,
                stage_timeout_seconds=30,
                test_timeout_seconds=60,
            )

            self.assertEqual(
                [
                    "search_plan",
                    "inspection_queue",
                    "daily_report_initial",
                    "strict_review",
                    "daily_report_sync",
                    "analytics_export",
                    "daily_html",
                    "workflow_html",
                    "targeted_tests",
                ],
                [stage.name for stage in stages],
            )
            self.assertTrue(all(stage.timeout_seconds > 0 for stage in stages))
            self.assertIn("--export-only", stages[5].argv)
            self.assertIn("--networking", stages[6].argv)
            self.assertIn(
                str(work / "outputs" / "2026-10-07-networking-matches.md"),
                stages[6].argv,
            )
            self.assertNotIn("discover_jobs_sharded.py", " ".join(stages[0].argv))

    def test_replay_plan_rejects_non_positive_timeouts(self) -> None:
        with self.assertRaisesRegex(ValueError, "timeouts"):
            build_replay_stages(
                repo_root=Path("/repo"),
                work_dir=Path("/work"),
                source_output_dir=Path("/outputs"),
                run_date="2026-10-07",
                python_executable=Path("/python"),
                stage_timeout_seconds=0,
                test_timeout_seconds=60,
            )


if __name__ == "__main__":
    unittest.main()
