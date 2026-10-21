from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from benchmark import (
    BenchmarkError,
    BenchmarkRunner,
    BonsaiChatClient,
    CostRates,
    ModelClient,
    OpenAIResponsesClient,
    UrlLibTransport,
    load_benchmark_cases,
    load_env_value,
    render_markdown,
)

POC_ROOT = Path(__file__).resolve().parent
DEFAULT_INSTRUCTIONS = (
    "Act as a skeptical senior software engineer. Analyze only the supplied facts. "
    "Identify failure modes, recommend a concrete approach, and state how to verify it. "
    "Use the headings Findings, Recommendation, and Verification. Stay under 350 words."
)
DEFAULT_RATES = CostRates(
    input_per_million=2.0,
    cached_input_per_million=0.1,
    cache_write_per_million=2.5,
    output_per_million=10.0,
)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare a local Bonsai model with OpenAI on identical reasoning tasks."
    )
    parser.add_argument("--cases", type=Path, default=POC_ROOT / "cases.json")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--provider",
        dest="providers",
        action="append",
        choices=("openai", "bonsai"),
        help="Run only the named provider; repeat for both. Defaults to both.",
    )
    parser.add_argument("--max-output-tokens", type=int, default=1200)
    parser.add_argument("--output-dir", type=Path, default=POC_ROOT / "results")
    parser.add_argument("--openai-model", default="gpt-6.1-sol")
    parser.add_argument(
        "--openai-reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default="low",
    )
    parser.add_argument("--openai-env-file", type=Path)
    parser.add_argument("--bonsai-model", default="Bonsai-27B-Q1_0")
    parser.add_argument("--bonsai-base-url", default="https://llmserver:8444/v1")
    parser.add_argument(
        "--bonsai-ca-file",
        type=Path,
        default=Path.home() / ".config/bonsai/llmserver.crt",
    )
    parser.add_argument("--bonsai-keychain-service", default="bonsai-aider-api-key")
    return parser.parse_args(argv)


def resolve_openai_key(env_file: Path | None) -> str:
    environment_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if environment_key:
        return environment_key
    if env_file is None:
        raise BenchmarkError(
            "OPENAI_API_KEY is not set and --openai-env-file was not supplied"
        )
    return load_env_value(env_file, "OPENAI_API_KEY")


def resolve_bonsai_key(service: str) -> str:
    environment_key = os.environ.get("BONSAI_API_KEY", "").strip()
    if environment_key:
        return environment_key
    completed = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BenchmarkError(f"Could not retrieve Bonsai key from Keychain service {service}")
    key = completed.stdout.strip()
    if not key:
        raise BenchmarkError(f"Keychain service {service} returned an empty key")
    return key


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        selected_providers = tuple(args.providers or ("openai", "bonsai"))
        cases = load_benchmark_cases(args.cases)
        transport = UrlLibTransport()
        providers: list[ModelClient] = []
        if "openai" in selected_providers:
            providers.append(
                OpenAIResponsesClient(
                    api_key=resolve_openai_key(args.openai_env_file),
                    model=args.openai_model,
                    transport=transport,
                    reasoning_effort=args.openai_reasoning_effort,
                )
            )
        if "bonsai" in selected_providers:
            if not args.bonsai_ca_file.is_file():
                raise BenchmarkError(
                    f"Bonsai CA certificate does not exist: {args.bonsai_ca_file}"
                )
            providers.append(
                BonsaiChatClient(
                    api_key=resolve_bonsai_key(args.bonsai_keychain_service),
                    model=args.bonsai_model,
                    base_url=args.bonsai_base_url,
                    ca_file=args.bonsai_ca_file,
                    transport=transport,
                )
            )
        report = BenchmarkRunner(
            providers=providers,
            instructions=DEFAULT_INSTRUCTIONS,
            max_output_tokens=args.max_output_tokens,
        ).run(cases, args.runs)

        args.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path = args.output_dir / f"benchmark-{timestamp}.json"
        markdown_path = args.output_dir / f"benchmark-{timestamp}.md"
        json_path.write_text(
            json.dumps(report.to_json_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(render_markdown(report, DEFAULT_RATES), encoding="utf-8")
        print(markdown_path)
        return 0
    except (BenchmarkError, ValueError) as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
