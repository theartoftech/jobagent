from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from experiment import ParityMetrics  # noqa: E402
from run_live_experiment import _load_external_source_status, main, prepare_live_inputs  # noqa: E402


class LiveInputPreparationTests(unittest.TestCase):
    def test_rejects_unsafe_or_invalid_external_source_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_json = root / "invalid.json"
            invalid_json.write_text("{")
            with self.assertRaisesRegex(ValueError, "Cannot read"):
                _load_external_source_status(invalid_json)
            not_an_object = root / "array.json"
            not_an_object.write_text("[]")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                _load_external_source_status(not_an_object)
            missing_boundary = root / "missing.json"
            missing_boundary.write_text("{}")
            with self.assertRaisesRegex(ValueError, "requires application_submission"):
                _load_external_source_status(missing_boundary)
            unsafe = root / "unsafe.json"
            unsafe.write_text(
                json.dumps(
                    {"application_submission": {"status": "enabled", "mode": "mutation"}}
                )
            )
            with self.assertRaisesRegex(ValueError, "must prohibit"):
                _load_external_source_status(unsafe)

    def test_prepares_isolated_state_previous_review_and_validated_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            output = repo / "outputs"
            work = root / "work"
            scripts = repo / "scripts"
            output.mkdir(parents=True)
            scripts.mkdir()
            wrapper = scripts / "jobagent_python.sh"
            wrapper.write_text("#!/bin/sh\n")
            for state_name in ("ats-sentinel-state.json", "company-deep-scan-state.json"):
                (output / state_name).write_text(json.dumps({"state": state_name}))
            (output / "2026-10-06-workflow-review.json").write_text(
                json.dumps({"reviews": []})
            )
            (output / "2026-10-07-workflow-review.json").write_text(
                json.dumps({"reviews": [{"url": "https://example.test/job"}]})
            )
            source_doc = output / "resumes/resume.docx"
            source_doc.parent.mkdir()
            source_doc.write_bytes(b"validated resume")
            (output / "2026-10-07-resume-production.json").write_text(
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

            result = prepare_live_inputs(
                repo_root=repo,
                work_dir=work,
                run_date="2026-10-07",
                local_date=date(2026, 10, 7),
            )

            self.assertEqual(1, result["seeded_document_count"])
            self.assertTrue((work / "state/ats-sentinel-state.json").is_file())
            self.assertTrue((work / "outputs/2026-10-06-workflow-review.json").is_file())
            self.assertTrue((work / "outputs/2026-10-07-resume-production.json").is_file())
            self.assertEqual(
                b"validated resume",
                (work / "outputs/resumes/resume.docx").read_bytes(),
            )
            boundary = json.loads(
                (work / "outputs/2026-10-07-external-source-status.json").read_text()
            )
            self.assertEqual("not_exercised", boundary["ai_apply"]["status"])
            self.assertEqual("read_only", boundary["live_ats"]["mode"])
            self.assertEqual("export_only", boundary["analytics"]["mode"])

    def test_first_run_of_day_uses_prior_manifests_without_requiring_a_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            output = repo / "outputs"
            work = root / "work"
            scripts = repo / "scripts"
            output.mkdir(parents=True)
            scripts.mkdir()
            (scripts / "jobagent_python.sh").write_text("#!/bin/sh\n")
            for state_name in ("ats-sentinel-state.json", "company-deep-scan-state.json"):
                (output / state_name).write_text(json.dumps({"state": state_name}))
            (output / "2026-10-07-workflow-review.json").write_text(
                json.dumps({"reviews": []})
            )
            document = output / "resumes/prior.docx"
            document.parent.mkdir()
            document.write_bytes(b"prior validated resume")
            (output / "2026-10-07-resume-production.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "artifact_path": "outputs/resumes/prior.docx",
                                "validation_status": "validated",
                            }
                        ]
                    }
                )
            )

            result = prepare_live_inputs(
                repo_root=repo,
                work_dir=work,
                run_date="2026-10-08",
                local_date=date(2026, 10, 8),
            )

            self.assertIsNone(result["baseline_workflow_review"])
            self.assertEqual("not_available", result["baseline_status"])
            self.assertEqual("2026-10-07-resume-production.json", result["seeded_manifests"]["resume"])
            self.assertEqual(b"prior validated resume", (work / "outputs/resumes/prior.docx").read_bytes())

    def test_rejects_non_current_run_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            with self.assertRaisesRegex(ValueError, "current local date"):
                prepare_live_inputs(
                    repo_root=repo,
                    work_dir=Path(directory) / "work",
                    run_date="2026-10-06",
                    local_date=date(2026, 10, 7),
                )

    def test_rejects_source_outputs_as_work_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            output = repo / "outputs"
            output.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "isolated"):
                prepare_live_inputs(
                    repo_root=repo,
                    work_dir=output,
                    run_date="2026-10-07",
                    local_date=date(2026, 10, 7),
                )

    def test_copies_a_validated_external_source_status_into_the_staged_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            output = repo / "outputs"
            scripts = repo / "scripts"
            output.mkdir(parents=True)
            scripts.mkdir()
            (scripts / "jobagent_python.sh").write_text("#!/bin/sh\n")
            for name in ("ats-sentinel-state.json", "company-deep-scan-state.json"):
                (output / name).write_text("{}")
            (output / "2026-10-07-workflow-review.json").write_text('{"reviews": []}')
            status = root / "status.json"
            status.write_text(
                json.dumps(
                    {
                        "linkedin": {"status": "completed", "raw_listing_count": 40},
                        "ai_apply": {"status": "skipped", "reason": "reauthorization required"},
                        "application_submission": {"status": "prohibited", "mode": "no_mutation"},
                    }
                )
            )

            result = prepare_live_inputs(
                repo_root=repo,
                work_dir=root / "work",
                run_date="2026-10-08",
                local_date=date(2026, 10, 8),
                external_source_status_input=status,
            )

            copied = Path(str(result["external_source_status"]))
            self.assertEqual("completed", json.loads(copied.read_text())["linkedin"]["status"])
            self.assertEqual("2026-10-08-external-source-status.json", copied.name)


class LiveMainTests(unittest.TestCase):
    def test_main_runs_pipeline_and_writes_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            work = root / "work"
            output = work / "outputs"
            repo.mkdir()
            output.mkdir(parents=True)
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({"reviews": []}))
            prepared = {
                "baseline_workflow_review": str(baseline),
                "external_source_status": str(work / "external-source-status.json"),
            }
            pipeline = MagicMock()
            pipeline.run.return_value = SimpleNamespace(
                status="passed",
                stages=(SimpleNamespace(status="passed"), SimpleNamespace(status="passed")),
                duration_ms=42,
            )
            metrics = {"discovered_jobs": 3}
            with (
                patch("run_live_experiment.prepare_live_inputs", return_value=prepared),
                patch("run_live_experiment.build_live_stages", return_value=(object(),)),
                patch("run_live_experiment.PipelineRunner", return_value=pipeline),
                patch("run_live_experiment.collect_live_metrics", return_value=metrics),
                patch(
                    "run_live_experiment.compare_workflow_reviews",
                    return_value=ParityMetrics(3, {"APPLY": 3}, 3),
                ),
            ):
                exit_code = main(
                    [
                        "--repo-root",
                        str(repo),
                        "--work-dir",
                        str(work),
                    ]
                )

            self.assertEqual(0, exit_code)
            result = json.loads((work / "experiment-a-live-result.json").read_text())
            self.assertEqual("passed", result["status"])
            self.assertEqual(2, result["executed_stage_count"])
            self.assertEqual("export_only", result["analytics_mode"])
            self.assertEqual("exact", result["baseline_parity"]["status"])
            self.assertEqual(metrics, result["metrics"])

    def test_main_reports_preparation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "run_live_experiment.prepare_live_inputs",
                side_effect=ValueError("deliberate preparation failure"),
            ):
                exit_code = main(
                    [
                        "--repo-root",
                        directory,
                        "--work-dir",
                        str(Path(directory) / "work"),
                    ]
                )

            self.assertEqual(1, exit_code)


if __name__ == "__main__":
    unittest.main()
