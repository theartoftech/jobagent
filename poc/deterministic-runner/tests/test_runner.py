from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from runner import (  # noqa: E402
    PipelineFailed,
    PipelineRunner,
    StageSpec,
    SubprocessExecutor,
)


class StageSpecTests(unittest.TestCase):
    def test_rejects_invalid_stage_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "name"):
            StageSpec(name="", argv=("python3",), timeout_seconds=1)
        with self.assertRaisesRegex(ValueError, "argv"):
            StageSpec(name="empty", argv=(), timeout_seconds=1)
        with self.assertRaisesRegex(ValueError, "timeout"):
            StageSpec(name="timeout", argv=("python3",), timeout_seconds=0)
        with self.assertRaisesRegex(ValueError, "environment"):
            StageSpec(
                name="environment",
                argv=("python3",),
                timeout_seconds=1,
                environment=(("", "value"),),
            )


class PipelineRunnerTests(unittest.TestCase):
    def test_rejects_missing_cwd_empty_pipeline_and_empty_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "cwd"):
                PipelineRunner(SubprocessExecutor(), root / "missing", root / "manifest.json")
            runner = PipelineRunner(SubprocessExecutor(), root, root / "manifest.json")
            with self.assertRaisesRegex(ValueError, "at least one"):
                runner.run((), run_id="run", metadata={})
            stage = StageSpec("valid", (sys.executable, "-c", "print('ok')"), 5)
            with self.assertRaisesRegex(ValueError, "run_id"):
                runner.run((stage,), run_id="", metadata={})

    def test_runs_serially_and_records_required_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            manifest = root / "manifest.json"
            stages = (
                StageSpec(
                    name="first",
                    argv=(sys.executable, "-c", f"from pathlib import Path; Path({str(first)!r}).write_text('one')"),
                    timeout_seconds=5,
                    required_outputs=(first,),
                ),
                StageSpec(
                    name="second",
                    argv=(
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; p=Path({str(first)!r}); Path({str(second)!r}).write_text(p.read_text() + ' two')",
                    ),
                    timeout_seconds=5,
                    required_outputs=(second,),
                ),
            )

            result = PipelineRunner(
                executor=SubprocessExecutor(),
                cwd=root,
                manifest_path=manifest,
            ).run(stages, run_id="serial-test", metadata={"mode": "test"})

            self.assertEqual("passed", result.status)
            self.assertEqual(["passed", "passed"], [stage.status for stage in result.stages])
            self.assertEqual("one two", second.read_text())
            written = json.loads(manifest.read_text())
            self.assertEqual("serial-test", written["run_id"])
            self.assertEqual(2, len(written["stages"]))
            self.assertEqual(64, len(written["stages"][1]["artifacts"][0]["sha256"]))

    def test_stage_environment_is_explicit_and_propagated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "environment.txt"
            stage = StageSpec(
                name="environment",
                argv=(
                    sys.executable,
                    "-c",
                    f"import os; from pathlib import Path; Path({str(output)!r}).write_text(os.environ['POC_STAGE_VALUE'])",
                ),
                timeout_seconds=5,
                required_outputs=(output,),
                environment=(("POC_STAGE_VALUE", "isolated"),),
            )

            PipelineRunner(SubprocessExecutor(), root, root / "manifest.json").run(
                (stage,),
                run_id="environment-test",
                metadata={},
            )

            self.assertEqual("isolated", output.read_text())

    def test_fails_closed_and_does_not_run_later_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forbidden = root / "must-not-exist.txt"
            manifest = root / "manifest.json"
            stages = (
                StageSpec(
                    name="failure",
                    argv=(sys.executable, "-c", "import sys; print('bad', file=sys.stderr); raise SystemExit(7)"),
                    timeout_seconds=5,
                ),
                StageSpec(
                    name="later",
                    argv=(sys.executable, "-c", f"from pathlib import Path; Path({str(forbidden)!r}).touch()"),
                    timeout_seconds=5,
                ),
            )

            with self.assertRaises(PipelineFailed) as raised:
                PipelineRunner(SubprocessExecutor(), root, manifest).run(
                    stages,
                    run_id="failure-test",
                    metadata={},
                )

            self.assertEqual("failed", raised.exception.result.status)
            self.assertEqual(7, raised.exception.result.stages[0].exit_code)
            self.assertIn("bad", raised.exception.result.stages[0].stderr_tail)
            self.assertFalse(forbidden.exists())
            self.assertEqual("failed", json.loads(manifest.read_text())["status"])

    def test_timeout_is_recorded_and_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            stage = StageSpec(
                name="slow",
                argv=(sys.executable, "-c", "import time; time.sleep(2)"),
                timeout_seconds=0.05,
            )

            with self.assertRaises(PipelineFailed) as raised:
                PipelineRunner(SubprocessExecutor(), root, manifest).run(
                    (stage,),
                    run_id="timeout-test",
                    metadata={},
                )

            self.assertEqual("timed_out", raised.exception.result.stages[0].status)
            self.assertIn("0.05", raised.exception.result.stages[0].error)

    def test_missing_required_output_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            missing = root / "missing.txt"
            stage = StageSpec(
                name="missing_output",
                argv=(sys.executable, "-c", "print('completed')"),
                timeout_seconds=5,
                required_outputs=(missing,),
            )

            with self.assertRaises(PipelineFailed) as raised:
                PipelineRunner(SubprocessExecutor(), root, manifest).run(
                    (stage,),
                    run_id="missing-output-test",
                    metadata={},
                )

            self.assertEqual("failed", raised.exception.result.stages[0].status)
            self.assertIn("missing required output", raised.exception.result.stages[0].error)

    def test_empty_required_output_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            empty = root / "empty.txt"
            stage = StageSpec(
                name="empty_output",
                argv=(
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(empty)!r}).touch()",
                ),
                timeout_seconds=5,
                required_outputs=(empty,),
            )

            with self.assertRaises(PipelineFailed) as raised:
                PipelineRunner(SubprocessExecutor(), root, manifest).run(
                    (stage,),
                    run_id="empty-output-test",
                    metadata={},
                )

            self.assertIn("required output is empty", raised.exception.result.stages[0].error)

    def test_resume_reuses_only_verified_stage_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            counter = root / "counter.txt"
            output = root / "output.txt"
            code = (
                "from pathlib import Path; "
                f"c=Path({str(counter)!r}); n=int(c.read_text())+1 if c.exists() else 1; c.write_text(str(n)); "
                f"Path({str(output)!r}).write_text(str(n))"
            )
            stage = StageSpec(
                name="resumable",
                argv=(sys.executable, "-c", code),
                timeout_seconds=5,
                required_outputs=(output,),
            )
            runner = PipelineRunner(SubprocessExecutor(), root, manifest)
            runner.run((stage,), run_id="first", metadata={})
            resumed = runner.run((stage,), run_id="second", metadata={}, resume=True)

            self.assertEqual("1", counter.read_text())
            self.assertEqual("reused", resumed.stages[0].status)

            output.write_text("tampered")
            rerun = runner.run((stage,), run_id="third", metadata={}, resume=True)
            self.assertEqual("2", counter.read_text())
            self.assertEqual("passed", rerun.stages[0].status)


if __name__ == "__main__":
    unittest.main()
