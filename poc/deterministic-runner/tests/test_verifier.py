from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from verifier import (  # noqa: E402
    build_verification_bundle,
    build_verifier_prompt,
    calculate_savings,
    parse_codex_events,
    serialize_compact_bundle,
    validate_verdict,
    verifier_output_schema,
)


class VerificationBundleTests(unittest.TestCase):
    def test_builds_compact_bundle_with_stage_and_error_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "outputs"
            output.mkdir()
            external = output / "2026-10-08-external-source-status.json"
            external.write_text(
                json.dumps(
                    {
                        "analytics": {"status": "enabled", "mode": "export_only"},
                        "ai_apply": {"status": "not_exercised", "mode": "unavailable"},
                        "application_submission": {"status": "prohibited", "mode": "no_mutation"},
                    }
                )
            )
            (output / "2026-10-08-discovered-jobs.json").write_text(
                json.dumps(
                    {
                        "jobs": [{"title": "ignored raw job"}],
                        "errors": [
                            {"source": "public-search", "message": "request failed with exit code 28"},
                            {"source": "workday", "message": "HTTP_422 422"},
                        ],
                    }
                )
            )
            manifest = {
                "status": "passed",
                "duration_ms": 1234,
                "metadata": {
                    "model_calls_inside_runner": 0,
                    "inputs": {"external_source_status": str(external)},
                },
                "stages": [
                    {
                        "name": "live_discovery",
                        "status": "passed",
                        "duration_ms": 1000,
                        "exit_code": 0,
                        "error": "",
                        "artifacts": [
                            {"path": str(output / "artifact.json"), "size_bytes": 20, "sha256": "abc"}
                        ],
                    }
                ],
            }
            result = {
                "status": "passed",
                "stage_count": 1,
                "executed_stage_count": 1,
                "analytics_mode": "export_only",
                "model_calls_inside_runner": 0,
                "metrics": {"discovered_jobs": 1, "discovery_errors": 2},
                "baseline_parity": {"status": "exact"},
                "external_source_status": str(external),
            }
            (root / "experiment-a-live-manifest.json").write_text(json.dumps(manifest))
            (root / "experiment-a-live-result.json").write_text(json.dumps(result))

            bundle = build_verification_bundle(root, "2026-10-08")
            serialized = serialize_compact_bundle(bundle, max_bytes=20_000)

            self.assertEqual("passed", bundle["controller"]["status"])
            self.assertEqual(1, len(bundle["stages"]))
            self.assertEqual(2, bundle["discovery_exceptions"]["total"])
            self.assertEqual(1, bundle["discovery_exceptions"]["by_category"]["curl_timeout"])
            self.assertEqual(1, bundle["discovery_exceptions"]["by_source"]["workday"])
            self.assertNotIn("ignored raw job", serialized)
            self.assertLess(len(serialized.encode("utf-8")), 20_000)
            self.assertTrue(bundle["deterministic_assertions"]["analytics_mode_declared"])

    def test_rejects_nonpassing_controller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outputs").mkdir()
            (root / "experiment-a-live-manifest.json").write_text(
                json.dumps({"status": "failed", "metadata": {}, "stages": []})
            )
            (root / "experiment-a-live-result.json").write_text(
                json.dumps({"status": "failed"})
            )
            (root / "external-source-status.json").write_text("{}")
            (root / "outputs/2026-10-08-discovered-jobs.json").write_text(
                json.dumps({"jobs": [], "errors": []})
            )

            with self.assertRaisesRegex(ValueError, "must pass"):
                build_verification_bundle(root, "2026-10-08")

    def test_rejects_bundle_over_size_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds"):
            serialize_compact_bundle({"value": "x" * 100}, max_bytes=20)


class CodexEventTests(unittest.TestCase):
    def test_parses_single_turn_usage_and_no_tools(self) -> None:
        events = "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "fresh-thread"}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 1200,
                            "cached_input_tokens": 100,
                            "output_tokens": 80,
                        },
                    }
                ),
            )
        )

        measurement = parse_codex_events(events)

        self.assertEqual("fresh-thread", measurement.thread_id)
        self.assertEqual(1200, measurement.input_tokens)
        self.assertEqual(100, measurement.cached_input_tokens)
        self.assertEqual(80, measurement.output_tokens)
        self.assertEqual(0, measurement.tool_calls)
        self.assertEqual(1, measurement.completed_turns)

    def test_counts_tool_calls_and_rejects_missing_usage(self) -> None:
        tool_events = "\n".join(
            (
                json.dumps({"type": "item.started", "item": {"type": "command_execution"}}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                    }
                ),
            )
        )
        self.assertEqual(1, parse_codex_events(tool_events).tool_calls)
        with self.assertRaisesRegex(ValueError, "usage"):
            parse_codex_events(json.dumps({"type": "thread.started"}))
        with self.assertRaisesRegex(ValueError, "Invalid Codex JSONL"):
            parse_codex_events("not-json")


class SavingsAndVerdictTests(unittest.TestCase):
    def test_calculates_token_and_response_reductions(self) -> None:
        savings = calculate_savings(
            baseline_input_tokens=16_051_827,
            baseline_output_tokens=25_225,
            baseline_responses=115,
            verifier_input_tokens=4_000,
            verifier_output_tokens=300,
            verifier_responses=1,
        )
        self.assertGreater(savings["input_token_reduction_percent"], 99.9)
        self.assertGreater(savings["total_token_reduction_percent"], 99.9)
        self.assertGreater(savings["response_reduction_percent"], 99.0)

    def test_validates_supported_verdict_and_rejects_invalid_shape(self) -> None:
        verdict = {
            "status": "verified_with_known_gaps",
            "checks": [
                {"name": "stages", "status": "passed", "evidence": "14/14 passed"}
            ],
            "exceptions": [],
            "summary": "Controller evidence is internally consistent.",
            "recommended_action": "retain human approval",
        }
        validate_verdict(verdict)
        with self.assertRaisesRegex(ValueError, "status"):
            validate_verdict({**verdict, "status": "invented"})

    def test_builds_bounded_tool_free_prompt_and_strict_schema(self) -> None:
        serialized = '{"controller":{"status":"passed"}}'
        prompt = build_verifier_prompt(serialized)
        schema = verifier_output_schema()
        self.assertIn("Do not call tools", prompt)
        self.assertIn(serialized, prompt)
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("verified_with_known_gaps", schema["properties"]["status"]["enum"])

    def test_rejects_invalid_savings_baselines_and_missing_verdict_checks(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            calculate_savings(
                baseline_input_tokens=0,
                baseline_output_tokens=0,
                baseline_responses=1,
                verifier_input_tokens=1,
                verifier_output_tokens=1,
                verifier_responses=1,
            )
        with self.assertRaisesRegex(ValueError, "checks"):
            validate_verdict(
                {
                    "status": "verified",
                    "checks": [],
                    "exceptions": [],
                    "summary": "summary",
                    "recommended_action": "action",
                }
            )


if __name__ == "__main__":
    unittest.main()
