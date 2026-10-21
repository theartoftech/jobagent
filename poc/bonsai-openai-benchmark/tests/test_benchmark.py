from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch
from urllib.error import HTTPError, URLError

POC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC_ROOT))

from benchmark import (  # noqa: E402
    BenchmarkCase,
    BenchmarkError,
    BenchmarkRunner,
    BonsaiChatClient,
    CostRates,
    ModelResponseError,
    OpenAIResponsesClient,
    ProviderResult,
    ProviderUsage,
    UrlLibTransport,
    estimate_openai_cost,
    load_benchmark_cases,
    load_env_value,
    render_markdown,
    score_result,
)


class FakeTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self._responses = responses
        self.requests: list[tuple[str, Mapping[str, str], Mapping[str, Any]]] = []

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        ca_file: Path | None = None,
    ) -> Mapping[str, Any]:
        self.requests.append((url, headers, payload))
        if not self._responses:
            raise AssertionError("Unexpected request")
        return self._responses.pop(0)


class FakeHttpResponse:
    def __init__(self, body: str) -> None:
        self._body = body

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def completed_openai_response(text: str = "explicit error and retry policy") -> Mapping[str, Any]:
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {
                "cached_tokens": 20,
                "cache_write_tokens": 10,
            },
            "output_tokens": 40,
            "output_tokens_details": {"reasoning_tokens": 12},
            "total_tokens": 140,
        },
    }


def completed_bonsai_response(text: str = "explicit error and retry policy") -> Mapping[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": text},
            }
        ],
        "usage": {
            "prompt_tokens": 90,
            "completion_tokens": 30,
            "total_tokens": 120,
        },
    }


class ProviderUsageTests(unittest.TestCase):
    def test_rejects_negative_tokens(self) -> None:
        with self.assertRaises(ValueError):
            ProviderUsage(input_tokens=-1, output_tokens=1, total_tokens=0)

    def test_rejects_inconsistent_total(self) -> None:
        with self.assertRaises(ValueError):
            ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=14)

    def test_rejects_reasoning_above_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "reasoning_tokens"):
            ProviderUsage(10, 5, 15, reasoning_tokens=6)

    def test_rejects_input_details_above_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "Input token details"):
            ProviderUsage(10, 5, 15, cached_input_tokens=8, cache_write_tokens=3)


class TransportTests(unittest.TestCase):
    def test_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(ValueError, "timeout_seconds"):
            UrlLibTransport(timeout_seconds=0)

    @patch("urllib.request.urlopen")
    def test_posts_and_parses_json_object(self, urlopen: Any) -> None:
        urlopen.return_value = FakeHttpResponse('{"ok": true}')

        result = UrlLibTransport(timeout_seconds=5).post_json(
            "https://example.test/v1",
            {"Authorization": "Bearer secret"},
            {"input": "hello"},
        )

        self.assertEqual(result, {"ok": True})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"input": "hello"})

    @patch("ssl.create_default_context")
    @patch("urllib.request.urlopen")
    def test_uses_custom_ca_file(self, urlopen: Any, create_context: Any) -> None:
        urlopen.return_value = FakeHttpResponse('{"ok": true}')
        context = object()
        create_context.return_value = context

        UrlLibTransport().post_json(
            "https://example.test/v1", {}, {}, Path("/tmp/test-ca.pem")
        )

        create_context.assert_called_once_with(cafile="/tmp/test-ca.pem")
        self.assertIs(urlopen.call_args.kwargs["context"], context)

    @patch("urllib.request.urlopen")
    def test_wraps_http_error(self, urlopen: Any) -> None:
        urlopen.side_effect = HTTPError(
            "https://example.test",
            429,
            "rate limited",
            {},
            io.BytesIO(b'{"error":"limit"}'),
        )

        with self.assertRaisesRegex(BenchmarkError, "HTTP 429"):
            UrlLibTransport().post_json("https://example.test", {}, {})

    @patch("urllib.request.urlopen", side_effect=URLError("offline"))
    def test_wraps_network_error(self, _urlopen: Any) -> None:
        with self.assertRaisesRegex(BenchmarkError, "offline"):
            UrlLibTransport().post_json("https://example.test", {}, {})

    @patch("urllib.request.urlopen")
    def test_rejects_invalid_json(self, urlopen: Any) -> None:
        urlopen.return_value = FakeHttpResponse("not json")

        with self.assertRaisesRegex(BenchmarkError, "invalid JSON"):
            UrlLibTransport().post_json("https://example.test", {}, {})

    @patch("urllib.request.urlopen")
    def test_rejects_non_object_json(self, urlopen: Any) -> None:
        urlopen.return_value = FakeHttpResponse("[]")

        with self.assertRaisesRegex(BenchmarkError, "non-object"):
            UrlLibTransport().post_json("https://example.test", {}, {})


class OpenAIClientTests(unittest.TestCase):
    def test_generates_and_parses_response_usage(self) -> None:
        transport = FakeTransport([completed_openai_response()])
        client = OpenAIResponsesClient(
            api_key="secret",
            model="gpt-6.1-sol",
            transport=transport,
            reasoning_effort="low",
        )

        result = client.generate("Be concise.", "Analyze this.", 500)

        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.usage.reasoning_tokens, 12)
        self.assertEqual(result.usage.cached_input_tokens, 20)
        self.assertEqual(result.usage.cache_write_tokens, 10)
        url, headers, payload = transport.requests[0]
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertFalse(payload["store"])

    def test_rejects_incomplete_response(self) -> None:
        response = dict(completed_openai_response())
        response["status"] = "incomplete"
        client = OpenAIResponsesClient("secret", "gpt-6.1-sol", FakeTransport([response]))

        with self.assertRaisesRegex(ModelResponseError, "incomplete"):
            client.generate("instructions", "prompt", 100)

    def test_rejects_response_without_usage(self) -> None:
        response = dict(completed_openai_response())
        del response["usage"]
        client = OpenAIResponsesClient("secret", "gpt-6.1-sol", FakeTransport([response]))

        with self.assertRaisesRegex(ModelResponseError, "usage"):
            client.generate("instructions", "prompt", 100)

    def test_rejects_response_without_text(self) -> None:
        response = dict(completed_openai_response())
        response["output"] = []
        client = OpenAIResponsesClient("secret", "gpt-6.1-sol", FakeTransport([response]))

        with self.assertRaisesRegex(ModelResponseError, "text"):
            client.generate("instructions", "prompt", 100)

    def test_rejects_invalid_constructor_values(self) -> None:
        transport = FakeTransport([])
        with self.assertRaisesRegex(ValueError, "key"):
            OpenAIResponsesClient("", "model", transport)
        with self.assertRaisesRegex(ValueError, "model"):
            OpenAIResponsesClient("key", "", transport)
        with self.assertRaisesRegex(ValueError, "reasoning"):
            OpenAIResponsesClient("key", "model", transport, "none")

    def test_rejects_invalid_usage_value(self) -> None:
        response = dict(completed_openai_response())
        response["usage"] = {
            "input_tokens": "bad",
            "output_tokens": 1,
            "total_tokens": 1,
        }
        client = OpenAIResponsesClient("secret", "model", FakeTransport([response]))

        with self.assertRaisesRegex(ModelResponseError, "usage was invalid"):
            client.generate("instructions", "prompt", 100)


class BonsaiClientTests(unittest.TestCase):
    def test_generates_and_parses_chat_completion(self) -> None:
        transport = FakeTransport([completed_bonsai_response()])
        client = BonsaiChatClient(
            api_key="bonsai-secret",
            model="Bonsai-27B-Q1_0",
            base_url="https://llmserver:8444/v1",
            ca_file=Path("/tmp/ca.pem"),
            transport=transport,
        )

        result = client.generate("Be concise.", "Analyze this.", 500)

        self.assertEqual(result.provider, "bonsai")
        self.assertEqual(result.usage.total_tokens, 120)
        url, headers, payload = transport.requests[0]
        self.assertEqual(url, "https://llmserver:8444/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer bonsai-secret")
        self.assertEqual(payload["max_tokens"], 500)

    def test_rejects_truncated_completion(self) -> None:
        response = dict(completed_bonsai_response())
        response["choices"] = [
            {"finish_reason": "length", "message": {"content": "partial"}}
        ]
        client = BonsaiChatClient("secret", "model", "https://host/v1", None, FakeTransport([response]))

        with self.assertRaisesRegex(ModelResponseError, "token limit"):
            client.generate("instructions", "prompt", 100)

    def test_rejects_missing_usage(self) -> None:
        response = dict(completed_bonsai_response())
        del response["usage"]
        client = BonsaiChatClient("secret", "model", "https://host/v1", None, FakeTransport([response]))

        with self.assertRaisesRegex(ModelResponseError, "usage"):
            client.generate("instructions", "prompt", 100)

    def test_rejects_invalid_constructor_values(self) -> None:
        transport = FakeTransport([])
        with self.assertRaisesRegex(ValueError, "key"):
            BonsaiChatClient("", "model", "https://host/v1", None, transport)
        with self.assertRaisesRegex(ValueError, "model"):
            BonsaiChatClient("key", "", "https://host/v1", None, transport)
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            BonsaiChatClient("key", "model", "http://host/v1", None, transport)

    def test_rejects_empty_choices(self) -> None:
        client = BonsaiChatClient(
            "key", "model", "https://host/v1", None, FakeTransport([{"choices": []}])
        )

        with self.assertRaisesRegex(ModelResponseError, "choices"):
            client.generate("instructions", "prompt", 100)


class ConfigurationTests(unittest.TestCase):
    def test_load_env_value_supports_export_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text('OTHER=x\nexport OPENAI_API_KEY="test-key"\n', encoding="utf-8")

            self.assertEqual(load_env_value(path, "OPENAI_API_KEY"), "test-key")

    def test_load_env_value_rejects_missing_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text("OTHER=x\n", encoding="utf-8")

            with self.assertRaisesRegex(BenchmarkError, "OPENAI_API_KEY"):
                load_env_value(path, "OPENAI_API_KEY")

    def test_load_cases_rejects_empty_required_concepts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(
                json.dumps([{"name": "bad", "prompt": "test", "required_concepts": []}]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BenchmarkError, "required_concepts"):
                load_benchmark_cases(path)

    def test_load_cases_rejects_duplicate_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(
                json.dumps(
                    [
                        {"name": "same", "prompt": "one", "required_concepts": ["x"]},
                        {"name": "same", "prompt": "two", "required_concepts": ["y"]},
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BenchmarkError, "Duplicate"):
                load_benchmark_cases(path)

    def test_load_cases_accepts_valid_definition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(
                json.dumps(
                    [{"name": "one", "prompt": "prompt", "required_concepts": ["x"]}]
                ),
                encoding="utf-8",
            )

            self.assertEqual(load_benchmark_cases(path)[0].name, "one")

    def test_load_cases_rejects_missing_file_and_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaisesRegex(BenchmarkError, "does not exist"):
                load_benchmark_cases(missing)
            invalid = Path(directory) / "invalid.json"
            invalid.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkError, "invalid JSON"):
                load_benchmark_cases(invalid)

    def test_load_env_value_rejects_missing_file(self) -> None:
        with self.assertRaisesRegex(BenchmarkError, "does not exist"):
            load_env_value(Path("/does/not/exist"), "OPENAI_API_KEY")


class ScoringAndCostTests(unittest.TestCase):
    def test_scores_required_concepts_case_insensitively(self) -> None:
        case = BenchmarkCase("case", "prompt", ("Explicit Error", "retry policy", "timeout"))
        result = ProviderResult(
            provider="bonsai",
            model="model",
            text="Use an explicit error and a RETRY POLICY.",
            usage=ProviderUsage(10, 5, 15),
            latency_ms=25,
        )

        score = score_result(case, result)

        self.assertEqual(score.matched_concepts, ("Explicit Error", "retry policy"))
        self.assertEqual(score.score, 2 / 3)

    def test_estimates_cost_with_cache_breakdown(self) -> None:
        usage = ProviderUsage(
            input_tokens=1_000_000,
            output_tokens=100_000,
            total_tokens=1_100_000,
            cached_input_tokens=200_000,
            cache_write_tokens=100_000,
        )
        rates = CostRates(2.0, 0.1, 2.5, 10.0)

        self.assertAlmostEqual(estimate_openai_cost(usage, rates), 2.67)

    def test_rejects_negative_cost_rate(self) -> None:
        with self.assertRaisesRegex(ValueError, "Cost rates"):
            CostRates(-1.0, 0.0, 0.0, 0.0)


class RunnerTests(unittest.TestCase):
    def test_runs_each_case_for_each_provider(self) -> None:
        openai = OpenAIResponsesClient(
            "secret",
            "gpt-6.1-sol",
            FakeTransport([completed_openai_response(), completed_openai_response()]),
        )
        bonsai = BonsaiChatClient(
            "secret",
            "Bonsai-27B-Q1_0",
            "https://host/v1",
            None,
            FakeTransport([completed_bonsai_response(), completed_bonsai_response()]),
        )
        cases = [
            BenchmarkCase("one", "prompt one", ("explicit error",)),
            BenchmarkCase("two", "prompt two", ("retry policy",)),
        ]
        runner = BenchmarkRunner(
            providers=(openai, bonsai),
            instructions="instructions",
            max_output_tokens=500,
        )

        report = runner.run(cases, runs=1)

        self.assertEqual(len(report.results), 4)
        self.assertEqual(report.summary["openai"].request_count, 2)
        self.assertEqual(report.summary["bonsai"].total_tokens, 240)

    def test_rejects_non_positive_run_count(self) -> None:
        runner = BenchmarkRunner((), "instructions", 100)

        with self.assertRaisesRegex(ValueError, "runs"):
            runner.run([], runs=0)

    def test_markdown_discloses_token_noncomparability(self) -> None:
        openai = OpenAIResponsesClient(
            "secret", "gpt-6.1-sol", FakeTransport([completed_openai_response()])
        )
        runner = BenchmarkRunner((openai,), "instructions", 500)
        report = runner.run(
            [BenchmarkCase("one", "prompt", ("explicit error",))],
            runs=1,
        )

        markdown = render_markdown(report, CostRates(2.0, 0.1, 2.5, 10.0))

        self.assertIn("not directly comparable", markdown)
        self.assertIn("gpt-6.1-sol", markdown)
        self.assertIn("Estimated OpenAI cost", markdown)

    def test_report_serializes_to_plain_data(self) -> None:
        client = OpenAIResponsesClient(
            "secret", "gpt-6.1-sol", FakeTransport([completed_openai_response()])
        )
        report = BenchmarkRunner((client,), "instructions", 500).run(
            [BenchmarkCase("one", "prompt", ("explicit error",))], 1
        )

        serialized = report.to_json_dict()

        self.assertEqual(serialized["summary"]["openai"]["request_count"], 1)

    def test_rejects_invalid_runner_configuration(self) -> None:
        client = OpenAIResponsesClient("secret", "model", FakeTransport([]))
        with self.assertRaisesRegex(ValueError, "instructions"):
            BenchmarkRunner((client,), "", 100)
        with self.assertRaisesRegex(ValueError, "max_output_tokens"):
            BenchmarkRunner((client,), "instructions", 0)
        with self.assertRaisesRegex(ValueError, "unique"):
            BenchmarkRunner((client, client), "instructions", 100)

    def test_rejects_missing_cases_and_providers(self) -> None:
        client = OpenAIResponsesClient("secret", "model", FakeTransport([]))
        with self.assertRaisesRegex(ValueError, "case"):
            BenchmarkRunner((client,), "instructions", 100).run([], 1)
        with self.assertRaisesRegex(ValueError, "provider"):
            BenchmarkRunner((), "instructions", 100).run(
                [BenchmarkCase("one", "prompt", ("x",))], 1
            )


if __name__ == "__main__":
    unittest.main()
