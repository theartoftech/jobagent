from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from live_experiment import (  # noqa: E402
    build_live_stages,
    collect_live_metrics,
    promote_validated_outputs,
    seed_validated_documents,
)


class LivePlanTests(unittest.TestCase):
    def test_builds_complete_serial_core_with_safe_analytics_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            work = root / "work"
            repo.mkdir()
            work.mkdir()
            stages = build_live_stages(
                repo_root=repo,
                work_dir=work,
                run_date="2026-10-07",
                python_wrapper=repo / "scripts/jobagent_python.sh",
                discovery_timeout_seconds=900,
                stage_timeout_seconds=120,
                document_timeout_seconds=600,
                test_timeout_seconds=300,
                publish_analytics=False,
                analytics_ssh_host=None,
            )

            self.assertEqual(
                [
                    "live_discovery",
                    "search_plan",
                    "inspection_queue",
                    "daily_report_initial",
                    "tracker_before_review",
                    "strict_review",
                    "daily_report_sync",
                    "tracker_after_sync",
                    "networking",
                    "daily_html",
                    "documents",
                    "analytics",
                    "workflow_html",
                    "targeted_tests",
                ],
                [stage.name for stage in stages],
            )
            self.assertIn("--export-only", stages[11].argv)
            self.assertNotIn("--ssh-host", stages[11].argv)
            self.assertEqual(
                (("JOBAGENT_RENDERER_MODE", "docker"),),
                stages[10].environment,
            )
            self.assertIn("--sentinel-state", stages[0].argv)
            self.assertIn("--deep-scan-state", stages[0].argv)
            self.assertIn(
                "poc/deterministic-runner/tests/test_live_experiment.py", stages[13].argv
            )

    def test_analytics_publication_requires_explicit_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "analytics_ssh_host"):
            build_live_stages(
                repo_root=Path("/repo"),
                work_dir=Path("/work"),
                run_date="2026-10-07",
                python_wrapper=Path("/wrapper"),
                discovery_timeout_seconds=900,
                stage_timeout_seconds=120,
                document_timeout_seconds=600,
                test_timeout_seconds=300,
                publish_analytics=True,
                analytics_ssh_host=None,
            )

    def test_publication_stage_includes_explicit_host(self) -> None:
        stages = build_live_stages(
            repo_root=Path("/repo"),
            work_dir=Path("/work"),
            run_date="2026-10-07",
            python_wrapper=Path("/wrapper"),
            discovery_timeout_seconds=900,
            stage_timeout_seconds=120,
            document_timeout_seconds=600,
            test_timeout_seconds=300,
            publish_analytics=True,
            analytics_ssh_host="jobtracker-host",
        )
        self.assertIn("--ssh-host", stages[11].argv)
        self.assertIn("jobtracker-host", stages[11].argv)
        self.assertNotIn("--export-only", stages[11].argv)


class LiveArtifactTests(unittest.TestCase):
    def test_promotion_preserves_linked_detail_pages_and_nested_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "work/outputs"
            production = root / "repo/outputs"
            details = staging / "2026-10-08-workflow-review-details"
            (details / "assets").mkdir(parents=True)
            for kind in ("resume", "cover-letter"):
                (staging / f"2026-10-08-{kind}-production.json").write_text(
                    json.dumps({"records": []})
                )
            report = staging / "2026-10-08-workflow-review.html"
            report.write_text('<a href="2026-10-08-workflow-review-details/cvs.html">CVS</a>')
            (details / "cvs.html").write_text('<img src="assets/chart.svg">')
            (details / "assets/chart.svg").write_bytes(b"<svg/>")
            previous = staging / "2026-10-07-workflow-review-details"
            previous.mkdir()
            (previous / "old.html").write_text("old report")
            unrelated = staging / "unrelated"
            unrelated.mkdir()
            (unrelated / "note.txt").write_text("unrelated")

            promoted = promote_validated_outputs(
                repo_root=root / "repo",
                work_dir=root / "work",
                run_date="2026-10-08",
            )

            for source in (report, details / "cvs.html", details / "assets/chart.svg"):
                target = production / source.relative_to(staging)
                self.assertIn(str(target), promoted)
                self.assertEqual(source.read_bytes(), target.read_bytes())
            self.assertFalse((production / previous.name).exists())
            self.assertFalse((production / unrelated.name).exists())

    def test_invalid_document_evidence_prevents_promoting_detail_pages(self) -> None:
        cases: tuple[tuple[dict[str, object], str], ...] = (
            ({"records": {}}, "requires records"),
            ({"records": [None]}, "record must be an object"),
            ({"records": [{"validation_status": "validated"}]}, "no artifact_path"),
            ({"records": [{"validation_status": "validated", "artifact_path": "outputs/resumes/missing.docx"}]}, "no artifact_sha256"),
            ({"records": [{"validation_status": "validated", "artifact_path": "outputs/resumes/missing.docx", "artifact_sha256": "0" * 64}]}, "Missing validated document"),
        )
        for manifest, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                staging = root / "work/outputs"
                details = staging / "2026-10-08-workflow-review-details"
                details.mkdir(parents=True)
                (details / "cvs.html").write_text("detail page")
                (staging / "2026-10-08-resume-production.json").write_text(json.dumps(manifest))
                (staging / "2026-10-08-cover-letter-production.json").write_text(
                    json.dumps({"records": []})
                )

                with self.assertRaisesRegex((ValueError, FileNotFoundError), message):
                    promote_validated_outputs(
                        repo_root=root / "repo",
                        work_dir=root / "work",
                        run_date="2026-10-08",
                    )

                self.assertFalse((root / "repo/outputs").exists())

    def test_promotes_only_validated_current_run_outputs_and_rewrites_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            staging = work / "outputs"
            production = root / "repo/outputs"
            state = work / "state"
            (staging / "resumes").mkdir(parents=True)
            state.mkdir(parents=True)
            artifact = staging / "resumes/resume.docx"
            artifact.write_bytes(b"validated")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            (staging / "2026-10-08-resume-production.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "artifact_path": str(artifact),
                                "artifact_sha256": digest,
                                "validation_status": "validated",
                            }
                        ]
                    }
                )
            )
            (staging / "2026-10-08-cover-letter-production.json").write_text(
                json.dumps({"records": []})
            )
            (staging / "2026-10-08-workflow-review.json").write_text(
                json.dumps({"reviews": []})
            )
            (staging / "stale-unrelated.json").write_text("{}")
            (state / "ats-sentinel-state.json").write_text('{"ok": true}')

            promoted = promote_validated_outputs(
                repo_root=root / "repo",
                work_dir=work,
                run_date="2026-10-08",
            )

            self.assertGreaterEqual(len(promoted), 5)
            self.assertFalse((production / "stale-unrelated.json").exists())
            self.assertEqual(b"validated", (production / "resumes/resume.docx").read_bytes())
            manifest = json.loads(
                (production / "2026-10-08-resume-production.json").read_text()
            )
            self.assertEqual("outputs/resumes/resume.docx", manifest["records"][0]["artifact_path"])
            self.assertEqual('{"ok": true}', (production / "ats-sentinel-state.json").read_text())

    def test_promotion_rejects_hash_mismatch_without_writing_production(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "work/outputs"
            (staging / "resumes").mkdir(parents=True)
            artifact = staging / "resumes/resume.docx"
            artifact.write_bytes(b"tampered")
            (staging / "2026-10-08-resume-production.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "artifact_path": str(artifact),
                                "artifact_sha256": "0" * 64,
                                "validation_status": "validated",
                            }
                        ]
                    }
                )
            )
            (staging / "2026-10-08-cover-letter-production.json").write_text(
                json.dumps({"records": []})
            )

            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                promote_validated_outputs(
                    repo_root=root / "repo",
                    work_dir=root / "work",
                    run_date="2026-10-08",
                )

            self.assertFalse((root / "repo/outputs").exists())

    def test_promotion_rejects_unvalidated_document_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "work/outputs"
            staging.mkdir(parents=True)
            (staging / "2026-10-08-resume-production.json").write_text(
                json.dumps({"records": [{"validation_status": "failed"}]})
            )
            (staging / "2026-10-08-cover-letter-production.json").write_text(
                json.dumps({"records": []})
            )

            with self.assertRaisesRegex(ValueError, "requires validated records"):
                promote_validated_outputs(
                    repo_root=root / "repo",
                    work_dir=root / "work",
                    run_date="2026-10-08",
                )

    def test_seeds_validated_documents_from_relative_manifest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            work = root / "work"
            (source / "outputs/resumes").mkdir(parents=True)
            document = source / "outputs/resumes/resume.docx"
            document.write_bytes(b"docx")
            manifest = source / "outputs/2026-10-07-resume-production.json"
            manifest.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "artifact_path": "outputs/resumes/resume.docx",
                                "validation_status": "validated",
                            }
                        ]
                    }
                )
            )

            seeded = seed_validated_documents(
                repo_root=source,
                work_dir=work,
                manifest_path=manifest,
            )

            self.assertEqual(1, seeded)
            self.assertEqual(b"docx", (work / "outputs/resumes/resume.docx").read_bytes())

    def test_rejects_missing_validated_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "artifact_path": "outputs/resumes/missing.docx",
                                "validation_status": "validated",
                            }
                        ]
                    }
                )
            )
            with self.assertRaisesRegex(FileNotFoundError, "validated document"):
                seed_validated_documents(root, root / "work", manifest)

    def test_collects_live_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "2026-10-07-discovered-jobs.json").write_text(
                json.dumps({"jobs": [{}, {}], "errors": [{}]})
            )
            (output / "2026-10-07-workflow-review.json").write_text(
                json.dumps(
                    {
                        "reviews": [
                            {"workflow_decision": "STRONG APPLY", "should_generate_resume": True},
                            {"workflow_decision": "SKIP", "should_generate_resume": False},
                        ]
                    }
                )
            )
            for name in ("resume", "cover-letter"):
                (output / f"2026-10-07-{name}-production.json").write_text(
                    json.dumps({"records": [{"validation_status": "validated"}]})
                )

            metrics = collect_live_metrics(output, "2026-10-07")

            self.assertEqual(2, metrics["discovered_jobs"])
            self.assertEqual(1, metrics["discovery_errors"])
            self.assertEqual(2, metrics["reviewed_jobs"])
            self.assertEqual(1, metrics["resume_ready_jobs"])
            self.assertEqual(1, metrics["validated_resumes"])
            self.assertEqual(1, metrics["validated_cover_letters"])


if __name__ == "__main__":
    unittest.main()
