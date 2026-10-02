# Stage 2 — ClickHouse Analytical Substrate

> **Status: NEXT.** Rewritten to match the repository (ADR-022 option A, ADR-025, ADR-026). Stage 2 proper is *normalized events + metrics* (`docs/tasks/STAGE-2.md`, branch `stage-2-metrics`). The typed query layer and evidence objects (below) follow once metrics exist. See `STATUS.md`.

## Objective

Make ClickHouse the source of truth for analytical facts used by Mode A and Mode B, through named, typed, parameterised queries only (`INV-002`, `INV-003`).

## Actual schema

| Table / view | Layer | Status |
|---|---|---|
| `events` | raw, Stripe-shaped; envelope in `data`, typed dimension columns, partitioned by `run_id` | exists (Stage 1) |
| `v_charge_attempts` | view over `events` (`ok` is `UInt8`) | exists |
| `events_norm` | normalized DATA_MODEL envelope; the **only** table metrics, detection, cohorts and evaluation read (ADR-022 A) | Stage 2 (mapping: ADR-025) |
| `<db>_truth.ground_truth`, `<db>_truth.runs` | evaluation answer key, separate database (ADR-017) | exists |
| `metric_snapshots`, `anomalies`, `evidence`, `decisions`, `investigation_steps` | derived and append-only audit records | later stages, when their producers exist |

Objects are **not** split into per-object tables (`customers`, `charges`, …): relationships are preserved through the id columns on `events` (customer, payment method, payment intent, charge, refund, invoice, subscription) and the immutable object snapshot in `data`.

`incidents` change status, so they are stored **append-only** in ClickHouse: every transition is a new row, and the current state is a view returning the latest row per incident (ADR-028, `INV-010`). No separate operational store.

## Metrics

Defined in `docs/METRICS.md` (Stage 2): authorization rate, first-attempt authorization rate, technical failure rate, checkout conversion, renewal success, refund rate, duplicate-charge rate, fraud-flag rate, attempt volume, revenue collected (per currency). Rates always carry numerator and denominator; `value` is NULL when the denominator is 0.

**Not available until the simulator emits them:** latency, retry rate, 3DS/action rate, PSP error rate. Do not implement metrics without data (`docs/tasks/SIMULATOR-FIXES.md`, phase 5 decides).

## Time grain and sample floor

Per ADR-026: global 15 min, PSP 1 h, daily for slow drift; finer cohorts (PSP × country and deeper) have no standalone series and are evaluated pooled over a candidate window with a minimum of 30 attempts.

## Dimensions

PSP, country (customer and issuer, separate columns), currency, payment method, card brand, platform, app version, plan, channel. Missing values are the explicit value `unknown`.

## Query contracts

AI-facing layer = fixed, named functions, never arbitrary SQL:

```text
get_metric_history()   get_metric_snapshot()   breakdown_by_dimension()   compare_cohorts()
get_related_metrics()  get_incident_window()   get_similar_incidents()*
```
\* no data source yet (needs incident history).

## Evidence IDs

Every analytical result serialises to an evidence object: `evidence_id` (stable across replay), `type`, `query_name`, `time_window`, `dimensions`, `values`, `source_table`, `created_at`, plus an epistemic label (`observed` / `inferred` / `estimated` / `recommended`).

## Ground-truth isolation (test-enforced)

Metrics, detection, cohorts, Jev and agent code must never import or query `<db>_truth` / `ground_truth`. A test greps `src/anomalyos/events` and `src/anomalyos/metrics` for `_truth` and `ground_truth`; the same test is extended to each later package as it is created. Neither `run_id` nor `scenario_id` may reach detection or AI inputs.

## Acceptance

- deterministic SQL fixtures; metrics equal a Python reference on a hand-built fixture; zero-denominator and incomplete trailing windows tested
- no future-data leakage (`ingested_at <= as_of`)
- evidence references stable across replay
- Mode A and Mode B consume compact query results