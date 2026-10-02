# Task for Cursor — publish Stage 1, then build Stage 2 (normalized events + metrics)

Owner: Mark Dragunov · Prepared: 2026-09-28 · Repo: https://github.com/markdragunov/anomalyos

Read first, in this order: `AGENTS.md` → `docs/ARCHITECTURE.md` (layers 1–3, 11) →
`docs/DATA_MODEL.md` → `docs/DECISIONS.md` (ADR-013, 017, 020, 021, **022**, OQ-2, OQ-4) →
`docs/SIMULATION.md` → `docs/TESTING.md`. The rules in `AGENTS.md` are hard constraints.

Work in phases. **Stop and report at every ⛔ gate**; do not continue past a gate on your own.

---

## Phase 0 — Publish Stage 1 to GitHub · SUPERSEDED

Superseded by ADR-023 (single repository). Stage 0–1 now live in `github.com/markdragunov/anomalyos` on the
`consolidate-repo` branch and arrive in `main` through one PR that Mark merges. What remains of this phase:

- ⛔ **Gate 0:** CI must be green on that PR, including the live ClickHouse job (`ci.yml`, ClickHouse 25.8,
  `ANOMALYOS_RUN_INTEGRATION=1`). `tests/integration/test_sim_clickhouse_live.py` has never run against a real
  server (only on embedded ClickHouse); if it fails, fix it minimally and explain the root cause in the PR.
- Expected skip in CI: `test_load_and_sql_checks_on_embedded_clickhouse` (chdb is not installed, ADR-020).

---

## Phase 1 — Decide ADR-022 (event format) ⛔ Gate 1

ADR-022 is *proposed*, not accepted. Stage 2 depends on it. Ask Mark to choose:

- **A (recommended):** two layers — raw Stripe-shaped events stay as generated; a pure,
  versioned mapping produces the DATA_MODEL envelope; metrics read **only** the normalized layer.
- **B:** generate the DATA_MODEL envelope directly, drop Stripe shapes (rewrites Stage 1).
- **C:** adopt the Stripe shape as the logical contract (rewrites DATA_MODEL).

Record the answer by flipping ADR-022 to *accepted* (or rewriting it). The rest of this task
assumes **A**; if Mark picks B or C, stop and re-plan with him.

---

## Phase 2 — Normalized event layer (Architecture layer 1 → 2)

Branch `stage-2-metrics` from `main` after Stage 1 is merged.

**Purpose.** Turn raw events into the DATA_MODEL envelope so every later layer depends on one
logical contract, not on a vendor-shaped source.

**Input.** A Stage 1 run directory (`events.jsonl.gz`, stream order) or the raw rows already in
`<db>.events`. **Output.** `<db>.events_norm` (MergeTree, `PARTITION BY run_id`).

Module: `src/anomalyos/events/normalize.py` (new package — the layer now has code).
A pure function over the ordered raw stream (it may keep per-PaymentIntent state, e.g. to
number attempts); no I/O, no clock, no randomness.

Envelope fields (DATA_MODEL): `event_id` (derived deterministically from raw `evt_…` id +
normalized type), `event_type`, `schema_version`, `occurred_at` (= raw `created`),
`ingested_at`, `merchant_id`, `entity_id`, dimensions (`customer_country`, `issuer_country`,
`currency`, `psp`, `payment_method_type`, `card_brand`, `platform`, `app_version`, `plan_id`,
`channel`, `attempt_no`) with explicit `unknown`, measures (`amount_minor`, `currency`),
outcome (`status`, `decline_code?`, `error_code?`), plus `run_id`, `raw_event_id`.

Decisions to make and record (one ADR, next free number is **ADR-025**):
- `ingested_at`: deterministic — equal to `occurred_at` or `occurred_at + seeded lag`; never
  wall clock. Late/out-of-order events must be representable (Architecture layer 1).
- `merchant_id`: single synthetic merchant constant (e.g. `mer_sim_001`) until multi-merchant.
- `scenario_id` in the DATA_MODEL envelope ↔ `run_id` in Stage 1: pick one name, document it.
  It must never reach detection/AI inputs (ADR-017).
- DATA_MODEL has no field for the idempotency signal, but duplicate-charge detection needs it:
  add `idempotency_key_present` (bool) as an outcome attribute — this is a DATA_MODEL change →
  document it in DATA_MODEL + DECISIONS.

Starting mapping (adjust only with a written reason in the ADR):

| Raw | Normalized |
|---|---|
| `payment_intent.created`, `channel=checkout` | `checkout.started` |
| `payment_intent.created`, `channel=renewal` | `subscription.renewal_attempted` |
| `charge.succeeded` | `payment.authorized` + `payment.captured` (automatic capture) |
| `charge.failed`, issuer-side code (`card_declined`, `expired_card`, `incorrect_cvc`, `payment_method_provider_decline`) | `payment.declined` |
| `charge.failed`, `processing_error` | `payment.failed` (technical) |
| `charge.failed`, `decline_code=highest_risk_level` | `fraud.flagged` + `payment.declined` |
| renewal charge with `attempt_no > 1` | additionally `dunning.attempted` |
| `invoice.created` / `invoice.paid` / `invoice.payment_failed` | same names |
| `customer.subscription.updated` with period advance | `subscription.renewed` |
| `customer.subscription.updated` → `past_due` | `subscription.past_due` |
| `customer.subscription.deleted` | `subscription.canceled` |
| `refund.created` | `refund.requested` + `refund.succeeded` |
| `customer.created`, `payment_method.attached`, `customer.subscription.created`, `invoice.finalized`, `invoice.marked_uncollectible`, `payment_intent.{requires_action,succeeded,payment_failed,canceled}` | no normalized event (entity/state only) — list them explicitly in code as "intentionally unmapped" so an unknown raw type fails loudly |

`chargeback.opened` is in the vocabulary but not generated yet — note it, don't fake it.

Loader: extend `anomalyos-sim load` (or add `anomalyos normalize`) to write `events_norm`
idempotently per `run_id`, with SQL checks: every normalized row has a `raw_event_id` that exists
in `events`; every mapped raw type produced ≥ 1 normalized row; no empty-string dimensions.

Tests (`tests/unit/events/`): one golden test per mapping row; unknown raw type → error;
determinism (same stream → identical output); `attempt_no` numbering incl. retries and dunning;
explicit `unknown`; no `_truth`/scenario identifiers in output.

---

## Phase 3 — Metrics layer (Architecture layer 3)

Module: `src/anomalyos/metrics/`. **Aggregation runs in ClickHouse** (AGENTS.md rule 1);
Python builds parameterized SQL and returns compact series. Never pull raw rows into Python.

Contract: `compute(metric, version, start, end, grain, filters, group_by) →
[(window_start, dims…, numerator, denominator, value)]`.
- `value` is NULL when `denominator = 0` (never 0, never NaN).
- Every series row carries numerator and denominator (never a bare ratio).
- The last window is flagged `is_complete = false` when it extends past `end`.
- `filters`/`group_by` accept only dimensions from the DATA_MODEL cohort list; anything
  else raises. Identifiers are validated, values are bound as query parameters.
- One canonical definition per `(name, version)` in a registry; definitions are data (SQL
  fragments + allowed dims), versioned, and changing one bumps its version.

Initial metrics (define precisely in `docs/METRICS.md`):

| Metric | Numerator / denominator |
|---|---|
| `authorization_rate` v1 | `payment.authorized` / (`authorized` + `declined` + `failed`), per attempt |
| `first_attempt_authorization_rate` v1 | same, `attempt_no = 1` |
| `technical_failure_rate` v1 | `payment.failed` / all attempts |
| `checkout_conversion_rate` v1 | checkouts with ≥ 1 `payment.authorized` / `checkout.started` (windowed by checkout start) |
| `renewal_success_rate` v1 | `subscription.renewed` / `subscription.renewal_attempted` |
| `refund_rate` v1 | `refund.succeeded` count / `payment.captured` count, same window (document the lag caveat) |
| `duplicate_charge_rate` v1 | captured payments without idempotency key that follow a same-customer, same-amount capture within 60 s / captures |
| `fraud_flag_rate` v1 | `fraud.flagged` / all attempts |
| `attempt_volume` v1 | count of attempts (denominator = 1 per window, documented) |
| `revenue_collected_minor` v1 | sum `amount_minor` of `payment.captured`, grouped by `currency` (no cross-currency sums) |

**OQ-2 (time grain).** Measure, don't guess: on `--seed 42 --scale 1.0`, report attempts per
window for global, per-PSP and per-(PSP × customer_country) at 5 min / 15 min / 1 h, and the
share of cohort-windows below a minimum-sample floor (e.g. 30 attempts). Propose a default grain
per scope in an ADR. ⛔ **Gate 2:** show the table + proposal to Mark before building on it.

Tests (`tests/unit/metrics/` + one integration test):
- Each metric equals a straightforward Python reference computed over a small normalized
  fixture (tiny hand-built stream — not the simulator), including zero-denominator windows
  and an incomplete trailing window.
- Determinism; invalid dimension/metric/version → error; SQL-injection attempts in filter
  values are bound, not interpolated.
- Sanity on simulated data (embedded ClickHouse via optional `chdb` or opt-in integration):
  in the `scn_psp_auth_degradation` window, `authorization_rate` grouped by `psp` shows psp_beta
  clearly below psp_alpha/psp_gamma; android 5.14.0 shows lower `checkout_conversion_rate` but
  not lower `authorization_rate`. These tests may read the scenario *window* from
  `docs/SIMULATION.md`/catalog constants, **never** from `<db>_truth` — metrics code must not
  import or query ground truth (add a test that greps `src/anomalyos/metrics` and
  `src/anomalyos/events` for `_truth` / `ground_truth`).

---

## Phase 4 — Documentation

- `docs/ARCHITECTURE.md` stage table: layer 1 normalized, layer 3 metrics → Stage 2.
- `docs/DATA_MODEL.md`: any vocabulary/field change, with version bump.
- `docs/DECISIONS.md`: ADR-022 status, ADR-025 (normalization), ADR-026 (grain), others as needed.
- `docs/TESTING.md`: Stage 2 suite table. `docs/METRICS.md`: metric definitions.
- `docs/ARCHITECTURE.md` stage status → Stage 2; `README.md` status.

---

## Out of scope for Stage 2

Detection engine, cohort engine, Jev integration, incident engine, agent, API, UI, real
payment integrations. No new runtime dependency without an ADR (rule 7). Do not change the
simulator's output unless a mapping genuinely needs it; if you do, bump `GENERATOR_VERSION`
and explain why (it changes every digest).

## Acceptance

- [ ] Stage 1 PR green in CI (incl. live ClickHouse), merged by Mark.
- [ ] ADR-022 accepted; ADR-025 (normalization) and grain ADR written.
- [ ] `events_norm` built for a run; SQL checks all 0; reload idempotent.
- [ ] Every metric: registry entry, doc, reference test, zero-denominator + trailing-window tests.
- [ ] Metrics computed in ClickHouse; no raw rows in Python; no ground-truth access (test-enforced).
- [ ] `pytest` green locally and in CI; skipped tests listed with reasons in the PR.
- [ ] PR `stage-2-metrics` → `main` with a report: what was built, decisions taken, what is open.

## Report format (end of each phase)

What changed · decisions taken (with ADR ids) · test results (passed / failed / skipped + reason) ·
open questions for Mark. Keep it short.
