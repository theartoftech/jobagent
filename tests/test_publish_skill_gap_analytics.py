from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.publish_skill_gap_analytics import (
    AnalyticsPublishError,
    build_import_payload,
    build_posting,
    canonical_skill_key,
    publish_payload,
    publish_payload_via_ssh,
    write_import_payload,
)


class _Response:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class PublishSkillGapAnalyticsTests(unittest.TestCase):
    def test_build_payload_maps_all_reviews_and_resolution_states(self) -> None:
        source_bytes = b'{"fixture": true}\n'
        review = {
            "generated_on": "2026-07-23",
            "reviews": [
                {
                    "company": "Block",
                    "title": "Senior Software Engineer, Product Platform",
                    "url": "http://block.xyz/careers/jobs/5239566008?gh_jid=5239566008",
                    "location": "Bay Area / Remote",
                    "work_model": "Remote/Hybrid",
                    "source_decision": "Apply",
                    "source_fit_score": 88,
                    "workflow_decision": "STRONG APPLY",
                    "already_applied": False,
                    "required_gaps": ["Go"],
                    "preferred_gaps": ["Kafka", "Python backend professional experience"],
                    "resolved_gaps": {
                        "Go": "do_not_claim",
                        "Kafka": "posting_false_positive: Mentioned in an unrelated team description.",
                    },
                    "hard_blockers": [],
                    "risk_flags": [],
                },
                {
                    "company": "Acme",
                    "title": "Java Architect",
                    "url": "https://jobs.example.com/openings/REQ-42",
                    "location": "Atlanta",
                    "work_model": "Onsite",
                    "source_decision": "Maybe",
                    "source_fit_score": 72,
                    "workflow_decision": "MODERATE STRETCH",
                    "already_applied": True,
                    "required_gaps": [],
                    "preferred_gaps": [],
                    "resolved_gaps": {},
                    "hard_blockers": [],
                    "risk_flags": [],
                },
            ],
        }

        payload = build_import_payload(
            review=review,
            source_artifact_name="2026-07-23-workflow-review.json",
            source_bytes=source_bytes,
            workflow_profile_bytes=b"profile\n",
            expected_date="2026-07-23",
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["run_date"], "2026-07-23")
        self.assertEqual(payload["source_sha256"], hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(len(payload["postings"]), 2)
        block = next(item for item in payload["postings"] if item["company"] == "Block")
        self.assertEqual(block["requisition_id"], "5239566008")
        self.assertEqual(block["posting_key"], "req:5239566008")
        self.assertEqual(block["work_model"], "hybrid")
        self.assertEqual(block["source_decision"], "apply")
        self.assertEqual(block["strict_workflow_decision"], "strong_apply")
        self.assertEqual(block["primary_blocker"], "skill_gap")
        gaps = {item["skill_key"]: item for item in block["gaps"]}
        self.assertEqual(gaps["go"]["classification"], "required")
        self.assertTrue(gaps["go"]["hard_blocker"])
        self.assertEqual(gaps["go"]["resolution_state"], "do_not_claim")
        self.assertTrue(gaps["go"]["active"])
        self.assertEqual(gaps["kafka"]["resolution_state"], "posting_false_positive")
        self.assertFalse(gaps["kafka"]["active"])
        self.assertIn("unrelated team", gaps["kafka"]["resolution_evidence"])
        self.assertEqual(gaps["python"]["resolution_state"], "unanswered")
        self.assertTrue(gaps["python"]["active"])
        acme = next(item for item in payload["postings"] if item["company"] == "Acme")
        self.assertEqual(acme["source_decision"], "moderate_stretch")
        self.assertEqual(acme["gaps"], [])
        self.assertTrue(acme["already_applied"])
        self.assertEqual(payload["training_statuses"], [])

    def test_canonical_skill_key_is_stable_and_rejects_empty_values(self) -> None:
        self.assertEqual(canonical_skill_key("TypeScript professional experience"), "typescript")
        self.assertEqual(canonical_skill_key("MCP servers"), "mcp")
        self.assertEqual(canonical_skill_key("C++"), "c++")
        self.assertEqual(canonical_skill_key("Double-entry accounting"), "double-entry_accounting")
        with self.assertRaisesRegex(ValueError, "empty skill label"):
            canonical_skill_key("   ")

    def test_intuit_postings_with_a_shared_job_family_id_remain_distinct(self) -> None:
        common = {
            "company": "Intuit",
            "location": "Remote",
            "work_model": "Remote",
            "source_decision": "Maybe",
            "source_fit_score": 75,
            "workflow_decision": "MODERATE STRETCH",
            "already_applied": False,
            "required_gaps": [],
            "preferred_gaps": [],
            "resolved_gaps": {},
            "hard_blockers": [],
            "risk_flags": [],
        }
        traffic = build_posting(
            {
                **common,
                "title": "Staff Software Engineer - Traffic",
                "url": "https://jobs.intuit.com/job/san-diego/staff-software-engineer-traffic/27595/98200474400",
            }
        )
        trust_and_safety = build_posting(
            {
                **common,
                "title": "Principal Software Engineer, Trust & Safety",
                "url": "https://jobs.intuit.com/job/new-york/principal-software-engineer-trust-and-safety/27595/96922204080",
            }
        )

        self.assertEqual("98200474400", traffic["requisition_id"])
        self.assertEqual("96922204080", trust_and_safety["requisition_id"])
        self.assertNotEqual(traffic["posting_key"], trust_and_safety["posting_key"])

    def test_build_payload_rejects_unknown_decision_and_invalid_date(self) -> None:
        base_review = {
            "generated_on": "2026-07-23",
            "reviews": [{
                "company": "Acme", "title": "Engineer", "url": "https://example.com/jobs/1",
                "location": "Remote", "work_model": "Remote", "source_decision": "Maybe",
                "source_fit_score": 50, "workflow_decision": "SURPRISE", "already_applied": False,
                "required_gaps": [], "preferred_gaps": [], "resolved_gaps": {},
                "hard_blockers": [], "risk_flags": [],
            }],
        }
        with self.assertRaisesRegex(ValueError, "Unsupported strict workflow decision"):
            build_import_payload(
                review=base_review,
                source_artifact_name="review.json",
                source_bytes=b"{}",
                workflow_profile_bytes=b"profile",
                expected_date="2026-07-23",
            )
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_import_payload(
                review={**base_review, "generated_on": "2026-07-22"},
                source_artifact_name="review.json",
                source_bytes=b"{}",
                workflow_profile_bytes=b"profile",
                expected_date="2026-07-23",
            )

    def test_write_payload_is_deterministic(self) -> None:
        payload = {"schema_version": 1, "run_date": "2026-07-23", "postings": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.json"
            write_import_payload(path, payload)
            first = path.read_bytes()
            write_import_payload(path, payload)
            second = path.read_bytes()
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))

    @mock.patch("scripts.publish_skill_gap_analytics.urlopen")
    def test_publish_uses_bearer_token_and_requires_ok_response(self, mocked_urlopen: mock.Mock) -> None:
        mocked_urlopen.return_value = _Response({"ok": True, "import_summary": {"runs": {"unchanged": 1}}})
        response = publish_payload(
            endpoint="http://127.0.0.1:8086/api/analytics/workflow-runs",
            token="secret-token",
            payload={"schema_version": 1},
            timeout_seconds=10,
        )
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(response["ok"], True)

        mocked_urlopen.return_value = _Response({"ok": False, "error": "rejected"})
        with self.assertRaisesRegex(AnalyticsPublishError, "did not confirm success"):
            publish_payload(
                endpoint="http://127.0.0.1:8086/api/analytics/workflow-runs",
                token="secret-token",
                payload={"schema_version": 1},
                timeout_seconds=10,
            )

    def test_publish_rejects_missing_token_without_network_call(self) -> None:
        with self.assertRaisesRegex(AnalyticsPublishError, "JOBTRACKER_ANALYTICS_IMPORT_TOKEN"):
            publish_payload(
                endpoint="http://127.0.0.1:8086/api/analytics/workflow-runs",
                token="",
                payload={"schema_version": 1},
                timeout_seconds=10,
            )

    @mock.patch("scripts.publish_skill_gap_analytics.subprocess.run")
    def test_publish_via_ssh_keeps_token_remote(self, mocked_run: mock.Mock) -> None:
        mocked_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps({"ok": True, "import_summary": {"runs": {"inserted": 1}}}) + "\n",
            stderr="",
        )
        response = publish_payload_via_ssh(
            ssh_host="jobtracker-host",
            endpoint="http://127.0.0.1:8086/api/analytics/workflow-runs",
            payload={"schema_version": 1, "postings": []},
            timeout_seconds=30,
        )
        command = mocked_run.call_args.args[0]
        self.assertEqual(command[:2], ["ssh", "jobtracker-host"])
        self.assertEqual(len(command), 3)
        self.assertIn("python3 -c", command[2])
        self.assertNotIn("secret-token", " ".join(command))
        self.assertIn("jobtracker-analytics-import", " ".join(command))
        self.assertEqual(json.loads(mocked_run.call_args.kwargs["input"])["schema_version"], 1)
        self.assertTrue(response["ok"])

        mocked_run.return_value = mock.Mock(returncode=1, stdout="", stderr="secret is missing\n")
        with self.assertRaisesRegex(AnalyticsPublishError, "SSH analytics import failed"):
            publish_payload_via_ssh(
                ssh_host="jobtracker-host",
                endpoint="http://127.0.0.1:8086/api/analytics/workflow-runs",
                payload={"schema_version": 1},
                timeout_seconds=30,
            )


if __name__ == "__main__":
    unittest.main()
