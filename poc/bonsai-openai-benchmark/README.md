# Bonsai vs OpenAI reasoning POC

This benchmark sends identical, self-contained reasoning cases to the local
`Bonsai-27B-Q1_0` endpoint and OpenAI's `gpt-6.1-sol` Responses API. It records
provider-reported token usage, reasoning tokens where available, latency, a
simple required-concept score, and estimated OpenAI cost.

Token totals from the two providers are informative but not directly comparable
because the models can use different tokenizers. The decision question is how
many OpenAI tokens and dollars are avoided while preserving acceptable quality
and latency.

## Run

The OpenAI key is read at runtime and is never copied into this directory. To
reuse a credential from an existing local environment file:

```bash
../../.venv/bin/python run_benchmark.py \
  --openai-env-file /path/to/existing-project/.env.local
```

Results are written under `results/`, which is ignored by Git. The runner fails
instead of silently substituting providers when credentials, usage accounting,
TLS, or response output is invalid.

To diagnose one provider explicitly without treating it as a comparison:

```bash
../../.venv/bin/python run_benchmark.py --provider bonsai
```

Bonsai defaults to `https://llmserver:8444/v1` and sends requests to
`https://llmserver:8444/v1/chat/completions`. Use `--bonsai-base-url` to override
the base URL. The daily deterministic job-search controller does not call this
benchmark or Bonsai; its separate verifier uses `gpt-6-luna` by default.

## Test

```bash
../../.venv/bin/python -m unittest discover -s tests -v
```

For coverage, when the `coverage` module is available:

```bash
../../.venv/bin/python -m coverage run --source=benchmark -m unittest discover -s tests
../../.venv/bin/python -m coverage report --fail-under=90
```

## Interpreting the POC

Treat the required-concept score as a repeatable smoke metric, not a semantic
quality verdict. Inspect both model answers. A local route is acceptable only
when Bonsai's evidence is easy to verify and the final hosted-model verification
cost is materially lower than doing the original task entirely with OpenAI.
