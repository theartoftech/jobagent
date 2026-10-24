from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, cast


@dataclass(frozen=True)
class CodexMeasurement:
    thread_id: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    completed_turns: int
    tool_calls: int

    def to_json_dict(self) -> Mapping[str, Any]:
        return asdict(self)


def build_verification_bundle(live_work_dir: Path, run_date: str) -> Mapping[str, Any]:
    manifest = _load_object(live_work_dir / "experiment-a-live-manifest.json")
    result = _load_object(live_work_dir / "experiment-a-live-result.json")
    external_path_value = result.get("external_source_status")
    if isinstance(external_path_value, str) and external_path_value.strip():
        external_path = Path(external_path_value)
        if not external_path.is_absolute():
            external_path = live_work_dir / external_path
    else:
        external_path = live_work_dir / "external-source-status.json"
    external = _load_object(external_path)
    discovery = _load_object(
        live_work_dir / "outputs" / f"{run_date}-discovered-jobs.json"
    )
    if manifest.get("status") != "passed" or result.get("status") != "passed":
        raise ValueError("The deterministic controller must pass before Experiment B")
    stages = manifest.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError("Controller manifest requires at least one stage")
    if any(not isinstance(stage, dict) or stage.get("status") != "passed" for stage in stages):
        raise ValueError("Experiment B requires every controller stage to pass")
    errors = discovery.get("errors")
    if not isinstance(errors, list):
        raise ValueError("Discovery artifact requires an errors list")

    error_sources: Counter[str] = Counter()
    error_categories: Counter[str] = Counter()
    examples: list[Mapping[str, str]] = []
    for item in errors:
        if not isinstance(item, dict):
            raise ValueError("Discovery errors must be JSON objects")
        source = str(item.get("source", "unknown"))
        message = str(item.get("message", ""))
        error_sources[source] += 1
        error_categories[_error_category(message)] += 1
        if len(examples) < 8:
            examples.append(
                {
                    "source": source,
                    "company": str(item.get("company", "")),
                    "category": _error_category(message),
                    "message": message[:240],
                }
            )

    compact_stages: list[Mapping[str, Any]] = []
    for stage_value in stages:
        stage = cast(dict[str, Any], stage_value)
        artifacts = stage.get("artifacts", [])
        if not isinstance(artifacts, list):
            raise ValueError(f"Stage artifacts must be a list: {stage.get('name', '')}")
        compact_stages.append(
            {
                "name": str(stage.get("name", "")),
                "status": str(stage.get("status", "")),
                "duration_ms": _required_nonnegative_int(stage, "duration_ms"),
                "exit_code": stage.get("exit_code"),
                "artifact_count": len(artifacts),
                "artifacts": [
                    {
                        "name": Path(str(artifact.get("path", ""))).name,
                        "size_bytes": artifact.get("size_bytes"),
                        "sha256": artifact.get("sha256"),
                    }
                    for artifact in artifacts
                    if isinstance(artifact, dict)
                ],
            }
        )

    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("Controller manifest requires metadata")
    return {
        "schema_version": 1,
        "experiment": "B",
        "run_date": run_date,
        "controller": {
            "status": result.get("status"),
            "duration_ms": manifest.get("duration_ms"),
            "stage_count": result.get("stage_count"),
            "executed_stage_count": result.get("executed_stage_count"),
            "model_calls_inside_runner": result.get("model_calls_inside_runner"),
            "analytics_mode": result.get("analytics_mode"),
            "metrics": result.get("metrics"),
            "baseline_parity": result.get("baseline_parity"),
        },
        "stages": compact_stages,
        "discovery_exceptions": {
            "total": len(errors),
            "by_source": dict(sorted(error_sources.items())),
            "by_category": dict(sorted(error_categories.items())),
            "examples": examples,
        },
        "external_source_boundaries": external,
        "deterministic_assertions": {
            "all_stages_passed": True,
            "model_calls_inside_runner_zero": metadata.get("model_calls_inside_runner") == 0,
            "analytics_mode_declared": result.get("analytics_mode")
            in {"export_only", "publish"},
            "application_submission_prohibited": _nested_value(
                external, "application_submission", "status"
            )
            == "prohibited",
        },
    }


def serialize_compact_bundle(bundle: Mapping[str, Any], *, max_bytes: int) -> str:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    serialized = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    byte_count = len(serialized.encode("utf-8"))
    if byte_count > max_bytes:
        raise ValueError(f"Verification bundle exceeds {max_bytes} bytes: {byte_count}")
    return serialized


def parse_codex_events(jsonl: str) -> CodexMeasurement:
    thread_id = ""
    completed_turns = 0
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    tool_calls = 0
    usage_found = False
    for line_number, raw_line in enumerate(jsonl.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event: object = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid Codex JSONL at line {line_number}: {error}") from error
        if not isinstance(event, dict):
            raise ValueError(f"Codex event at line {line_number} must be an object")
        if event.get("type") == "thread.started":
            thread_id = str(event.get("thread_id", ""))
        if event.get("type") == "item.started" and _is_tool_item(event.get("item")):
            tool_calls += 1
        if event.get("type") == "turn.completed":
            completed_turns += 1
            usage = event.get("usage")
            if isinstance(usage, dict):
                input_tokens += _usage_int(usage, "input_tokens")
                cached_input_tokens += _usage_int(usage, "cached_input_tokens", required=False)
                output_tokens += _usage_int(usage, "output_tokens")
                usage_found = True
    if not usage_found:
        raise ValueError("Codex event stream does not contain completed-turn usage")
    return CodexMeasurement(
        thread_id=thread_id,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        completed_turns=completed_turns,
        tool_calls=tool_calls,
    )


def calculate_savings(
    *,
    baseline_input_tokens: int,
    baseline_output_tokens: int,
    baseline_responses: int,
    verifier_input_tokens: int,
    verifier_output_tokens: int,
    verifier_responses: int,
) -> Mapping[str, Any]:
    values = (
        baseline_input_tokens,
        baseline_output_tokens,
        baseline_responses,
        verifier_input_tokens,
        verifier_output_tokens,
        verifier_responses,
    )
    if any(value < 0 for value in values):
        raise ValueError("Token and response counts cannot be negative")
    if baseline_input_tokens == 0 or baseline_responses == 0:
        raise ValueError("Baseline input tokens and responses must be positive")
    baseline_total = baseline_input_tokens + baseline_output_tokens
    verifier_total = verifier_input_tokens + verifier_output_tokens
    return {
        "baseline": {
            "input_tokens": baseline_input_tokens,
            "output_tokens": baseline_output_tokens,
            "total_tokens": baseline_total,
            "responses": baseline_responses,
        },
        "experiment_b": {
            "input_tokens": verifier_input_tokens,
            "output_tokens": verifier_output_tokens,
            "total_tokens": verifier_total,
            "responses": verifier_responses,
        },
        "input_token_reduction_percent": _reduction_percent(
            baseline_input_tokens, verifier_input_tokens
        ),
        "total_token_reduction_percent": _reduction_percent(baseline_total, verifier_total),
        "response_reduction_percent": _reduction_percent(
            baseline_responses, verifier_responses
        ),
    }


def validate_verdict(verdict: Mapping[str, Any]) -> None:
    allowed_statuses = {"verified", "verified_with_known_gaps", "failed", "needs_human_review"}
    if verdict.get("status") not in allowed_statuses:
        raise ValueError("Verifier verdict has an unsupported status")
    checks = verdict.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Verifier verdict requires checks")
    allowed_check_statuses = {"passed", "failed", "not_verifiable"}
    for check in checks:
        if not isinstance(check, dict) or check.get("status") not in allowed_check_statuses:
            raise ValueError("Verifier check has an unsupported status")
        if not all(isinstance(check.get(key), str) and check.get(key) for key in ("name", "evidence")):
            raise ValueError("Verifier checks require name and evidence")
    exceptions = verdict.get("exceptions")
    if not isinstance(exceptions, list):
        raise ValueError("Verifier verdict requires an exceptions list")
    for key in ("summary", "recommended_action"):
        if not isinstance(verdict.get(key), str) or not str(verdict.get(key)).strip():
            raise ValueError(f"Verifier verdict requires {key}")


def verifier_output_schema() -> Mapping[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "checks", "exceptions", "summary", "recommended_action"],
        "properties": {
            "status": {
                "type": "string",
                "enum": ["verified", "verified_with_known_gaps", "failed", "needs_human_review"],
            },
            "checks": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "status", "evidence"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "status": {
                            "type": "string",
                            "enum": ["passed", "failed", "not_verifiable"],
                        },
                        "evidence": {"type": "string", "minLength": 1},
                    },
                },
            },
            "exceptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["severity", "stage", "detail"],
                    "properties": {
                        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                        "stage": {"type": "string", "minLength": 1},
                        "detail": {"type": "string", "minLength": 1},
                    },
                },
            },
            "summary": {"type": "string", "minLength": 1},
            "recommended_action": {"type": "string", "minLength": 1},
        },
    }


def build_verifier_prompt(serialized_bundle: str) -> str:
    return "\n".join(
        (
            "You are the fresh, read-only verifier for Job Agent Experiment B.",
            "Use only the compact JSON bundle below. Do not call tools, read files, browse, or edit anything.",
            "Check stage completeness, artifact evidence, safety assertions, tests represented by stages, and explicit source gaps.",
            "Do not reclassify jobs or authorize documents/applications.",
            "A passed controller with explicit unavailable sources or discovery errors should be verified_with_known_gaps, not fully verified.",
            "Use failed only for internal contradiction or failed evidence. Use needs_human_review for consequential ambiguity.",
            "Return only the requested schema.",
            "",
            serialized_bundle,
        )
    )


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read JSON object {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return cast(dict[str, Any], value)


def _error_category(message: str) -> str:
    if "exit code 28" in message:
        return "curl_timeout"
    if "exit code 47" in message:
        return "redirect_limit"
    if "exit code 6" in message:
        return "dns_failure"
    if "HTTP_422" in message or re.search(r"\b422\b", message):
        return "http_422"
    if re.search(r"\b404\b", message):
        return "http_404"
    if "unavailable" in message.lower():
        return "official_page_unavailable"
    if "decode" in message.lower():
        return "non_text_response"
    return "other"


def _required_nonnegative_int(value: Mapping[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool) or result < 0:
        raise ValueError(f"Expected non-negative integer field {key!r}")
    return result


def _nested_value(value: Mapping[str, Any], outer: str, inner: str) -> object:
    nested = value.get(outer)
    return nested.get(inner) if isinstance(nested, dict) else None


def _usage_int(usage: Mapping[str, Any], key: str, *, required: bool = True) -> int:
    value = usage.get(key)
    if value is None and not required:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"Codex usage requires non-negative integer {key}")
    return value


def _is_tool_item(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return value.get("type") in {
        "command_execution",
        "mcp_tool_call",
        "file_change",
        "web_search",
    }


def _reduction_percent(baseline: int, candidate: int) -> float:
    if baseline <= 0:
        raise ValueError("Reduction baseline must be positive")
    return round((1 - (candidate / baseline)) * 100, 4)
