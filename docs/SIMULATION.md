# Stage 1 — Synthetic billing world & ground truth

The simulator is the answer key for every later stage. It generates a Stripe-shaped event
stream for one synthetic merchant and, separately, a machine-readable record of every
injected scenario. Shapes follow Stripe's public object model; **no compatibility with the
Stripe API is claimed**.

Code: `src/anomalyos/simulation/` · CLI: `anomalyos-sim` (`python -m anomalyos.simulation`).

## 1. Quickstart

```bash
anomalyos-sim generate --seed 42 --out data/run_42 --validate   # ≈1.37M events, ~1.5–2 min
anomalyos-sim generate --seed 42 --preset core --scale 0.1 --out data/core_small
anomalyos-sim validate --in data/run_42
set -a; source .env; set +a
anomalyos-sim load --in data/run_42 [--replace]                  # → ClickHouse + SQL checks
```

Output directory: `events.jsonl.gz` (one event envelope per line, stream order) ·
`ground_truth.json` · `manifest.json` (config, scenario spec hashes, `events_sha256`,
`truth_digest`, counts by type). Exit codes: `0` ok · `1` validation/check failure ·
`2` usage/config · `3` ClickHouse error.

## 2. Module map

| Module | Responsibility |
|---|---|
| `ids.py` | Structural-key IDs (`pi_…`, 24 base62) and derived seeds via BLAKE2b. No counters, no `hash()`. |
| `models.py` | Frozen dataclasses: Customer, PaymentMethod, PaymentIntent, Charge, Refund, Invoice, Subscription, Event. |
| `world.py` | Markets, rails, PSP routing, demand shape, release train. Pure reference data. |
| `scenarios.py` | `Effect` (mechanism × selector × intensity profile), `ScenarioSpec`, catalog, presets. |
| `engine.py` | Hour-stepped generator, per-payment lifecycle, counterfactual tally. |
| `ground_truth.py` | Closed vocabularies, `TruthSpec` → `GroundTruth`. |
| `runner.py` | Orchestration, digests, run directory, `run_id`. |
| `validate.py` | Stream validator (relationships, lifecycle, credentials, leakage) + truth validator. |
| `clickhouse_load.py` | DDL, flattening, loader (`clickhouse-connect`), SQL checks. |

## 3. World (baseline)

* 28 days from Monday 2026-08-03 00:00 UTC (configurable); 120 days of pre-history for
  customers, payment methods and subscriptions.
* 9 markets (US, GB, DE, FR, ES, NL, BR, MX, JP) with their own currency (incl. zero-decimal
  `jpy`), base approval, basket size, method mix (card, `sepa_debit`, `ideal`, `pix`) and brand mix.
* 3 generic PSPs (`psp_alpha|beta|gamma`) with country-dependent, sticky-per-customer card routing;
  local methods have fixed rails.
* Demand: local-time diurnal × weekday × payday shape, Poisson per market-hour.
  Approval: market × brand × funding × PSP × daily jitter (σ≈0.4 %) × month-end dip.
* Checkout lifecycle: `payment_intent.created` (idempotency key) → optional 3DS
  `requires_action` → charge → retry (35 %) → cancel after 1 h if unpaid; organic abandonment,
  refunds (3 %, 1–10 d lag) and rare non-idempotent duplicates.
* Renewals: ~50 % of customers subscribe monthly; `invoice.created` → `finalized` → MIT charge →
  `invoice.paid` / `payment_failed` → dunning at +3 d, +7 d → `marked_uncollectible` +
  `customer.subscription.deleted`.
* Mobile release train with per-customer exponential update lag (mean 20 h).

Scale 1.0 ≈ 1.37M events (≈330k PaymentIntents, ≈30k renewals, 60k customers).

## 4. Event envelope

```json
{"id":"evt_…","object":"event","api_version":"2026-09-01.anomalyos-sim","created":1786000000,
 "livemode":false,"type":"payment_intent.payment_failed",
 "data":{"object":{"id":"pi_…", "...": "full snapshot"},"previous_attributes":{"status":"requires_confirmation"}},
 "request":{"id":"req_…","idempotency_key":null}}
```

`data.object` is the full object snapshot at emission time, serialised once — events are
immutable. Simulator-only attributes (PSP, platform, app version, channel, customer country)
live in `metadata`, where a merchant would put them.

Event types: `customer.created`, `payment_method.attached`, `customer.subscription.{created,updated,deleted}`,
`invoice.{created,finalized,paid,payment_failed,marked_uncollectible}`,
`payment_intent.{created,requires_action,succeeded,payment_failed,canceled}`,
`charge.{succeeded,failed,refunded}`, `refund.created`.

## 5. Scenario engine

A scenario is a set of **mechanisms** applied to individual payments, never a painted metric:

| Mechanism | Effect on each matching payment |
|---|---|
| `approval` | success probability × (1 − s(t)·(1 − m)); effect-caused declines use the effect's codes |
| `abandon` | + s(t)·a probability that the PaymentIntent is never confirmed |
| `refund` | + s(t)·r refund probability with a short lag |
| `duplicate` | + s(t)·d probability of a second charge without idempotency key |
| `volume` | extra organic demand × (1 + s(t)·(m − 1)) from an independent stream |
| `inject` | exogenous stream (card testing): new customers, tiny amounts, bot declines |

`s(t)` is a piecewise-linear intensity profile: `step`, `ramp`, `degrade_then_recover`.
Selectors combine `customer_country, psp, payment_method_type, card_brand, platform, app_version, channel`.

**Determinism & locality.** Each market-hour, each effect-hour and each renewal has its own
RNG seeded from `(world seed, structural key)`; every PaymentIntent consumes a fixed-length
uniform vector. A scenario therefore perturbs only its cohort and window — a test asserts that
the stream before the first injection is byte-identical with and without scenarios.

**Counterfactual truth.** The same uniforms are evaluated with and without each matching
effect (leave-one-out when effects overlap). The difference yields exact impact — lost
payments and amount, extra refunds, duplicates, fraud — computed by code from the mechanism.

### Catalog (`--preset full`; `core` = the four marked ★)

| Scenario | Window (from start) | Mechanism | Route |
|---|---|---|---|
| ★ normal_variation | d1 (24 h) | none — negative control | suppress |
| ★ psp_authorization_degradation | d2 14:00–17:00 | psp_beta approval ×0.65, step | incident |
| country_degradation | d4 09:00–13:00 | BR cards ×0.75, all PSPs | incident |
| payment_method_degradation | d6 18:00–22:00 | sepa_debit ×0.5 | incident |
| checkout_regression_app_version | d9 10:00 → hotfix d11 16:00 | android 5.14.0 abandon +40 % | incident |
| subscription_renewal_failure | d12 → d14 12:00 | renewals on psp_gamma cards ×0.55 | incident |
| refund_spike | d15 08:00–20:00 | FR refunds +25 %, 1–6 h lag | incident |
| duplicate_charge | d16 11:00–14:30 | web duplicates 12 % | incident |
| fraud_like_spike | d18 02:00–05:00 | 1 500 card-testing attempts, US/psp_alpha | incident |
| ★ gradual_degradation | d19 → end | ES×psp_gamma ramps to ×0.88 over 7 d | incident |
| harmless_seasonality | d20 12:00–d21 | BR demand ×2.2 (global approval dips by mix) | suppress |
| small_cohort_noisy_anomaly | d21 03:00–06:00 | JP amex ×0.35 on ~8 attempts | suppress |
| correlated_unrelated_anomalies | d22 10:00–16:00 | DE campaign ×1.8 **and** iDEAL ×0.6 | suppress + incident |
| ★ recovery_after_degradation | d24 20:00 → d25 01:00 | GB psp_alpha ×0.7, linear recovery | incident |

## 6. Ground truth

One record per truth (a scenario can hold several), in `ground_truth.json` and in the
**separate** ClickHouse database `<db>_truth` — never inside events:

`scenario_id, scenario_kind, record_key, incident_id, start, end, true_cause, root_cause
{cause, locus, mechanism, effect_ids}, expected_route, severity, affected_cohorts,
control_cohorts, expected_metric_effect {primary_metric, direction, parameters, measured},
expected_impact, expected_detection_window, expected_recovery, unrelated_to, scenario_seed,
spec_hash, generator_version`.

* `true_cause` uses the DATA_MODEL cause vocabulary v1 verbatim (test-enforced);
  `true_cause == root_cause.cause`. Harmless signals (seasonality, campaign, tiny-cohort noise,
  control day) are all `normal_variation`; `scenario_kind` keeps the finer distinction.
* Every record also carries the DATA_MODEL ground-truth fields: `scenario_type`, `seed`,
  `is_incident`, `onset_at`, `end_at`, `ramp` (`step|gradual|recovering|none`),
  `affected_metrics`, `injected_effect`, `true_impact {lost_revenue_minor, affected_payment_count}`.
* `expected_route ∈ {incident, watch, suppress}`. Suppressed records have no `incident_id`
  and no detection window; incidents always have both.
* `end = null` means still ongoing at world end (gradual degradation).
* Nothing in this file is produced by Jev or an LLM.

## 7. ClickHouse

`<db>.events` — MergeTree, `PARTITION BY run_id`, `ORDER BY (run_id, type, created, id)`,
typed dimension columns + `data` (exact event JSON, ZSTD). View `<db>.v_charge_attempts`.
Dimension columns use DATA_MODEL names; a dimension that does not apply is `unknown`, never
empty. Country is always named: `customer_country` and `issuer_country`.

> **Open (ADR-022).** This table is the *raw* layer. The normalized DATA_MODEL envelope
> (`event_type` vocabulary, `occurred_at`/`ingested_at`, `merchant_id`) is proposed as a pure
> mapping on top and is not built yet. Metrics should be written against whichever layer
> ADR-022 settles.
`<db>_truth.ground_truth`, `<db>_truth.runs`. Reload = drop partition + insert.

```sql
SELECT toStartOfHour(created) h, psp, avg(ok) approval, count() n
FROM anomalyos.v_charge_attempts
WHERE run_id = {run:String} AND channel = 'checkout'
GROUP BY h, psp ORDER BY h, psp;
```

SQL checks after every load (all must be 0): duplicate event ids, six dangling-reference
checks, charge≠PI amount, refunds>charge, PAN-like digit runs, truth-token leakage.

## 8. Acceptance → evidence

| Criterion | Evidence |
|---|---|
| seed reproduces identical dataset | `test_same_seed_reproduces_identical_dataset` (file-level SHA-256), manifest `events_sha256` |
| every scenario has machine-readable truth | `validate_ground_truth`, `test_every_scenario_has_valid_machine_readable_truth` |
| valid event/object relationships | `EventValidator` + SQL checks; negative tests prove they fire |
| no sensitive payment credentials | no PAN/CVC/expiry/IBAN/last4 fields; key scan + Luhn scan + SQL digit-run check |
| replay → identical expected outcomes | `test_replay_yields_identical_expected_outcomes` |
| 1M+ events, noise, cohorts, temporal patterns | scale 1.0 run: 1.37M events, see §3 |

## 9. Known simplifications

Fixed UTC offsets (no DST); 30-day billing months; issuer country = customer country;
one payment method per customer; iDEAL renewals are allowed (real iDEAL recurs via SEPA);
disputes and payouts not modelled yet.
