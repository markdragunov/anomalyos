# Evaluation harness

Structure for **deterministic, replayable** evaluation. This is not anomaly detection.

Eventual jobs: score dispositions and explanations against **explicit ground truth**, compare **baselines**, record **decision traces**, and measure **cost** and **latency**.

Today: validate scenario files and emit a smoke trace. `scripts/eval_smoke.py` is the entrypoint.

## Layout

| Path | Purpose |
| --- | --- |
| `evals/scenarios/` | JSON fixtures. Each file **must** include `ground_truth`. |
| `evals/baselines/` | Frozen traces/scores for regression (empty until a real runner exists). |
| `evals/benchmarks/` | Cross-scenario cost/latency/quality comparisons (empty until measured). |
| `evals/output/` | Generated traces (gitignored). |
| `evals/harness.py` | Load, validate, smoke-run. Stdlib only. |

## Scenario shape

Required keys:

- `id` — stable scenario ID
- `title` — short name
- `purpose` — why this fixture exists
- `mode` — `"A"` or `"B"`
- `inputs` — bounded structured payload (never a raw event stream)
- `ground_truth` — expected disposition and/or evidence constraints
- `budgets` — `max_tool_calls`, `max_tokens`, `max_latency_ms`
- `replay` — `deterministic` (bool) and optional `seed`

Optional: `notes`.

Replay means a future runtime can re-run the same `inputs` and compare the trace to `ground_truth` and to a baseline.

## Traces

Smoke traces (and later real traces) include:

- `scenario_id`
- `disposition` (from ground truth today; from policy later)
- `evidence_ids`
- `policy_version` (placeholder until policy exists)
- `budgets`
- `cost` (tokens / USD; zero in smoke)
- `latency_ms`
- `epistemic` breakdown when evidence exists

## What this harness does not do

- Detect anomalies
- Call Jev, ClickHouse, or an LLM
- Invent baseline scores
