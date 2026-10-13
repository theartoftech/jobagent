# Deterministic Job-Agent Runner POC

Experiment A replaces model-supervised command launching and polling with one
local, serial process runner. It provides hard per-stage timeouts, required
output validation, content hashes, full local logs, compact manifest tails, and
verified resume support.

Experiment B gives one fresh, ephemeral hosted verifier only a compact summary
of Experiment A's live result. The verifier is read-only, tool-free,
schema-constrained, and token-metered. It does not receive raw job descriptions
or the accumulated conversation history.

The initial experiment supports two modes:

- `run_experiment.py` is an isolated fixed-input replay. It uses the October 7
  discovery and applied-job snapshots, seeds the preceding strict review for
  carry-forward, and requires exact role-by-role decision and
  resume-authorization parity.
- `run_live_experiment.py` is an isolated live deterministic-core run. It runs
  live ATS discovery, JobTracker reconciliation, review, networking, rendering,
  validated document production, export-only analytics, and targeted tests.
  It records external sources that cannot be exercised by the local runner.

Neither mode submits applications. The live mode defaults analytics to
export-only; publication requires both `--publish-analytics` and an explicit
`--analytics-ssh-host`. AI Apply, BlitzAPI, and browser-board passes remain
explicitly outside the local runner until supported adapters exist.

## Run

```bash
.venv/bin/python poc/deterministic-runner/run_experiment.py \
  --run-date 2026-10-07 \
  --work-dir /private/tmp/jobagent-experiment-a
```

Live deterministic core:

```bash
.venv/bin/python poc/deterministic-runner/run_live_experiment.py \
  --run-date 2026-10-07 \
  --work-dir /private/tmp/jobagent-experiment-a-live-20261007
```

Fresh compact verifier, after the live controller passes:

```bash
.venv/bin/python poc/deterministic-runner/run_experiment_b.py \
  --live-work-dir /private/tmp/jobagent-experiment-a-live-20261008 \
  --output-dir /private/tmp/jobagent-experiment-b-20261008 \
  --run-date 2026-10-08 \
  --model gpt-6-luna \
  --reasoning-effort low
```

Experiment B uses the existing authenticated Codex CLI session. It does not
create, copy, or persist an API key in this repository.

## Test and coverage

```bash
.venv/bin/python -m unittest discover -s poc/deterministic-runner/tests -v
.venv/bin/python -m coverage run \
  --source=runner,experiment,live_experiment,run_live_experiment,verifier,run_experiment_b \
  -m unittest discover -s poc/deterministic-runner/tests
.venv/bin/python -m coverage report --fail-under=90
```

The October 7 live result is recorded in
`results/experiment-a-live-20261007.md`. The October 8 fresh-verifier result is
recorded in `results/experiment-b-20261008.md`. Generated result files are
ignored by Git.
