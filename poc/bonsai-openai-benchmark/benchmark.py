from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, cast


class BenchmarkError(RuntimeError):
    """Raised when the benchmark cannot produce a trustworthy result."""


class ModelResponseError(BenchmarkError):
    """Raised when a provider returns an incomplete or invalid response."""


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.reasoning_tokens,
            self.cached_input_tokens,
            self.cache_write_tokens,
        )
        if any(value < 0 for value in values):
            raise ValueError("Token counts must be non-negative")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens cannot exceed output_tokens")
        if self.cached_input_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("Input token details cannot exceed input_tokens")


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str
    text: str
    usage: ProviderUsage
    latency_ms: int


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    prompt: str
    required_concepts: tuple[str, ...]


@dataclass(frozen=True)
class QualityScore:
    matched_concepts: tuple[str, ...]
    missing_concepts: tuple[str, ...]
    score: float


@dataclass(frozen=True)
class BenchmarkResult:
    case_name: str
    run_number: int
    result: ProviderResult
    quality: QualityScore


@dataclass(frozen=True)
class ProviderSummary:
    model: str
    request_count: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    average_latency_ms: float
    average_quality_score: float


@dataclass(frozen=True)
class BenchmarkReport:
    created_at_utc: str
    results: tuple[BenchmarkResult, ...]
    summary: Mapping[str, ProviderSummary]

    def to_json_dict(self) -> Mapping[str, Any]:
        return {
            "created_at_utc": self.created_at_utc,
            "results": [asdict(result) for result in self.results],
            "summary": {
                provider: asdict(summary) for provider, summary in self.summary.items()
            },
        }


@dataclass(frozen=True)
class CostRates:
    input_per_million: float
    cached_input_per_million: float
    cache_write_per_million: float
    output_per_million: float

    def __post_init__(self) -> None:
        if any(value < 0 for value in asdict(self).values()):
            raise ValueError("Cost rates must be non-negative")


class JsonTransport(Protocol):
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        ca_file: Path | None = None,
    ) -> Mapping[str, Any]: ...


class ModelClient(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def generate(
        self,
        instructions: str,
        prompt: str,
        max_output_tokens: int,
    ) -> ProviderResult: ...


class UrlLibTransport:
    def __init__(self, timeout_seconds: int = 180) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        ca_file: Path | None = None,
    ) -> Mapping[str, Any]:
        context = ssl.create_default_context(cafile=str(ca_file)) if ca_file else None
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout_seconds,
                context=context,
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")[:500]
            raise BenchmarkError(
                f"HTTP {error.code} from {url}: {error_body}"
            ) from error
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as error:
            raise BenchmarkError(f"Request to {url} failed: {error}") from error

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise BenchmarkError(f"Provider at {url} returned invalid JSON") from error
        if not isinstance(parsed, dict):
            raise BenchmarkError(f"Provider at {url} returned a non-object JSON response")
        return cast(Mapping[str, Any], parsed)


class OpenAIResponsesClient:
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        transport: JsonTransport,
        reasoning_effort: str = "low",
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI API key cannot be empty")
        if not model.strip():
            raise ValueError("OpenAI model cannot be empty")
        if reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Unsupported OpenAI reasoning effort")
        self._api_key = api_key
        self._model = model
        self._transport = transport
        self._reasoning_effort = reasoning_effort

    @property
    def model(self) -> str:
        return self._model

    def generate(
        self,
        instructions: str,
        prompt: str,
        max_output_tokens: int,
    ) -> ProviderResult:
        _validate_generation_inputs(instructions, prompt, max_output_tokens)
        started = time.perf_counter()
        response = self._transport.post_json(
            "https://api.openai.com/v1/responses",
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": self._model,
                "instructions": instructions,
                "input": prompt,
                "max_output_tokens": max_output_tokens,
                "reasoning": {"effort": self._reasoning_effort},
                "store": False,
            },
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        status = response.get("status")
        if status != "completed":
            raise ModelResponseError(f"OpenAI response status was {status!r}, not completed")
        text = _extract_openai_text(response)
        usage = _parse_openai_usage(response.get("usage"))
        return ProviderResult(self.provider, self._model, text, usage, latency_ms)


class BonsaiChatClient:
    provider = "bonsai"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        ca_file: Path | None,
        transport: JsonTransport,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Bonsai API key cannot be empty")
        if not model.strip():
            raise ValueError("Bonsai model cannot be empty")
        if not base_url.startswith("https://"):
            raise ValueError("Bonsai base_url must use HTTPS")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._ca_file = ca_file
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    def generate(
        self,
        instructions: str,
        prompt: str,
        max_output_tokens: int,
    ) -> ProviderResult:
        _validate_generation_inputs(instructions, prompt, max_output_tokens)
        started = time.perf_counter()
        response = self._transport.post_json(
            f"{self._base_url}/chat/completions",
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_output_tokens,
            },
            self._ca_file,
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelResponseError("Bonsai response did not contain choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ModelResponseError("Bonsai response choice was invalid")
        if first.get("finish_reason") == "length":
            raise ModelResponseError("Bonsai response reached the token limit")
        message = first.get("message")
        if not isinstance(message, dict):
            raise ModelResponseError("Bonsai response did not contain a message")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise ModelResponseError("Bonsai response did not contain text")
        usage = _parse_bonsai_usage(response.get("usage"))
        return ProviderResult(
            self.provider,
            self._model,
            text.strip(),
            usage,
            latency_ms,
        )


class BenchmarkRunner:
    def __init__(
        self,
        providers: Sequence[ModelClient],
        instructions: str,
        max_output_tokens: int,
    ) -> None:
        if not instructions.strip():
            raise ValueError("instructions cannot be empty")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        provider_names = [provider.provider for provider in providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("Provider names must be unique")
        self._providers = tuple(providers)
        self._instructions = instructions
        self._max_output_tokens = max_output_tokens

    def run(self, cases: Sequence[BenchmarkCase], runs: int) -> BenchmarkReport:
        if runs <= 0:
            raise ValueError("runs must be positive")
        if not cases:
            raise ValueError("At least one benchmark case is required")
        if not self._providers:
            raise ValueError("At least one provider is required")

        results: list[BenchmarkResult] = []
        for run_number in range(1, runs + 1):
            for case in cases:
                for provider in self._providers:
                    provider_result = provider.generate(
                        self._instructions,
                        case.prompt,
                        self._max_output_tokens,
                    )
                    results.append(
                        BenchmarkResult(
                            case_name=case.name,
                            run_number=run_number,
                            result=provider_result,
                            quality=score_result(case, provider_result),
                        )
                    )

        return BenchmarkReport(
            created_at_utc=_utc_timestamp(),
            results=tuple(results),
            summary=_summarize(results),
        )


def load_env_value(path: Path, variable_name: str) -> str:
    if not path.is_file():
        raise BenchmarkError(f"Environment file does not exist: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        name, separator, value = line.partition("=")
        if separator and name.strip() == variable_name:
            normalized = value.strip()
            if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
                normalized = normalized[1:-1]
            if normalized:
                return normalized
            break
    raise BenchmarkError(f"{variable_name} is missing or empty in {path}")


def load_benchmark_cases(path: Path) -> tuple[BenchmarkCase, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BenchmarkError(f"Benchmark cases file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise BenchmarkError(f"Benchmark cases file is invalid JSON: {path}") from error
    if not isinstance(raw, list) or not raw:
        raise BenchmarkError("Benchmark cases must be a non-empty JSON array")

    cases: list[BenchmarkCase] = []
    names: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise BenchmarkError(f"Benchmark case {index} must be an object")
        name = item.get("name")
        prompt = item.get("prompt")
        concepts = item.get("required_concepts")
        if not isinstance(name, str) or not name.strip():
            raise BenchmarkError(f"Benchmark case {index} requires a name")
        if name in names:
            raise BenchmarkError(f"Duplicate benchmark case name: {name}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise BenchmarkError(f"Benchmark case {name} requires a prompt")
        if (
            not isinstance(concepts, list)
            or not concepts
            or any(not isinstance(concept, str) or not concept.strip() for concept in concepts)
        ):
            raise BenchmarkError(
                f"Benchmark case {name} requires non-empty required_concepts"
            )
        names.add(name)
        cases.append(BenchmarkCase(name, prompt, tuple(concepts)))
    return tuple(cases)


def score_result(case: BenchmarkCase, result: ProviderResult) -> QualityScore:
    normalized_text = result.text.casefold()
    matched = tuple(
        concept for concept in case.required_concepts if concept.casefold() in normalized_text
    )
    missing = tuple(
        concept for concept in case.required_concepts if concept.casefold() not in normalized_text
    )
    return QualityScore(matched, missing, len(matched) / len(case.required_concepts))


def estimate_openai_cost(usage: ProviderUsage, rates: CostRates) -> float:
    uncached_tokens = (
        usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens
    )
    return (
        uncached_tokens * rates.input_per_million
        + usage.cached_input_tokens * rates.cached_input_per_million
        + usage.cache_write_tokens * rates.cache_write_per_million
        + usage.output_tokens * rates.output_per_million
    ) / 1_000_000


def render_markdown(report: BenchmarkReport, rates: CostRates) -> str:
    lines = [
        "# Bonsai vs OpenAI Benchmark",
        "",
        f"Generated: {report.created_at_utc}",
        "",
        "Provider token counts are not directly comparable because tokenizers differ. "
        "The decision metric is OpenAI usage avoided at acceptable quality and latency.",
        "",
        "| Provider | Model | Requests | Input | Output | Reasoning | Total | Avg latency | Quality |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for provider, summary in sorted(report.summary.items()):
        lines.append(
            f"| {provider} | {summary.model} | {summary.request_count} | "
            f"{summary.input_tokens} | {summary.output_tokens} | "
            f"{summary.reasoning_tokens} | {summary.total_tokens} | "
            f"{summary.average_latency_ms:.0f} ms | "
            f"{summary.average_quality_score:.0%} |"
        )

    openai_results = [
        item.result.usage for item in report.results if item.result.provider == "openai"
    ]
    if openai_results:
        estimated_cost = sum(estimate_openai_cost(usage, rates) for usage in openai_results)
        lines.extend(
            [
                "",
                f"Estimated OpenAI cost for these requests: **${estimated_cost:.6f}**.",
            ]
        )

    lines.extend(["", "## Individual results", ""])
    for item in report.results:
        lines.extend(
            [
                f"### {item.case_name} — {item.result.provider} run {item.run_number}",
                "",
                f"- Model: `{item.result.model}`",
                f"- Tokens: {item.result.usage.total_tokens}",
                f"- Latency: {item.result.latency_ms} ms",
                f"- Required-concept score: {item.quality.score:.0%}",
                f"- Missing concepts: {', '.join(item.quality.missing_concepts) or 'none'}",
                "",
                item.result.text,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _validate_generation_inputs(
    instructions: str,
    prompt: str,
    max_output_tokens: int,
) -> None:
    if not instructions.strip():
        raise ValueError("instructions cannot be empty")
    if not prompt.strip():
        raise ValueError("prompt cannot be empty")
    if max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be positive")


def _extract_openai_text(response: Mapping[str, Any]) -> str:
    output = response.get("output")
    if not isinstance(output, list):
        raise ModelResponseError("OpenAI response did not contain output")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
    if not texts:
        raise ModelResponseError("OpenAI response did not contain text")
    return "\n".join(texts)


def _parse_openai_usage(raw_usage: Any) -> ProviderUsage:
    if not isinstance(raw_usage, dict):
        raise ModelResponseError("OpenAI response did not contain usage")
    details = raw_usage.get("input_tokens_details", {})
    output_details = raw_usage.get("output_tokens_details", {})
    if not isinstance(details, dict) or not isinstance(output_details, dict):
        raise ModelResponseError("OpenAI response usage details were invalid")
    try:
        return ProviderUsage(
            input_tokens=_required_int(raw_usage, "input_tokens", "OpenAI usage"),
            output_tokens=_required_int(raw_usage, "output_tokens", "OpenAI usage"),
            total_tokens=_required_int(raw_usage, "total_tokens", "OpenAI usage"),
            reasoning_tokens=_optional_int(output_details, "reasoning_tokens"),
            cached_input_tokens=_optional_int(details, "cached_tokens"),
            cache_write_tokens=_optional_int(details, "cache_write_tokens"),
        )
    except ValueError as error:
        raise ModelResponseError(f"OpenAI usage was invalid: {error}") from error


def _parse_bonsai_usage(raw_usage: Any) -> ProviderUsage:
    if not isinstance(raw_usage, dict):
        raise ModelResponseError("Bonsai response did not contain usage")
    try:
        return ProviderUsage(
            input_tokens=_required_int(raw_usage, "prompt_tokens", "Bonsai usage"),
            output_tokens=_required_int(raw_usage, "completion_tokens", "Bonsai usage"),
            total_tokens=_required_int(raw_usage, "total_tokens", "Bonsai usage"),
        )
    except ValueError as error:
        raise ModelResponseError(f"Bonsai usage was invalid: {error}") from error


def _required_int(source: Mapping[str, Any], key: str, label: str) -> int:
    value = source.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label}.{key} must be an integer")
    return value


def _optional_int(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key, 0)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _summarize(results: Sequence[BenchmarkResult]) -> Mapping[str, ProviderSummary]:
    grouped: dict[str, list[BenchmarkResult]] = {}
    for result in results:
        grouped.setdefault(result.result.provider, []).append(result)
    summaries: dict[str, ProviderSummary] = {}
    for provider, provider_results in grouped.items():
        count = len(provider_results)
        models = {result.result.model for result in provider_results}
        if len(models) != 1:
            raise BenchmarkError(f"Provider {provider} returned multiple models")
        summaries[provider] = ProviderSummary(
            model=models.pop(),
            request_count=count,
            input_tokens=sum(item.result.usage.input_tokens for item in provider_results),
            output_tokens=sum(item.result.usage.output_tokens for item in provider_results),
            reasoning_tokens=sum(
                item.result.usage.reasoning_tokens for item in provider_results
            ),
            total_tokens=sum(item.result.usage.total_tokens for item in provider_results),
            average_latency_ms=sum(
                item.result.latency_ms for item in provider_results
            )
            / count,
            average_quality_score=sum(item.quality.score for item in provider_results)
            / count,
        )
    return summaries


def _utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
