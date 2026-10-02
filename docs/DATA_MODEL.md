# Data Model

The **logical** contract for billing data. Physical ClickHouse DDL (engines, ordering keys,
partitions) is an implementation detail defined in Stage 1 migrations and may change;
this document should not.

## Conventions

| Concern | Rule |
|---|---|
| Money | Integer **minor units** (`amount_minor`) + ISO 4217 `currency`. Never floats. |
| Time | UTC. `occurred_at` (business time) and `ingested_at` (system time) are distinct. |
| IDs | Opaque strings, stable across runs for the same scenario + seed. |
| Enums | Closed, versioned vocabularies; unknown values are rejected, not coerced. |
| Nullability | A missing dimension is an explicit `unknown` value, not an empty string. |
| PII | Synthetic only. No real card numbers, names or emails, ever. Cards are represented by brand + BIN-country, never PAN. |

## Entities

| Entity | Key attributes |
|---|---|
| Merchant | `merchant_id`, `country`, `vertical` |
| Customer | `customer_id`, `merchant_id`, `country`, `signup_at` |
| Subscription plan | `plan_id`, `merchant_id`, `interval`, `price_minor`, `currency` |
| Subscription | `subscription_id`, `customer_id`, `plan_id`, `status`, `current_period_end` |
| Invoice | `invoice_id`, `subscription_id?`, `customer_id`, `amount_minor`, `currency`, `status`, `due_at` |
| Payment method | `payment_method_id`, `customer_id`, `type` (card, wallet, bank_debit, local APM), `card_brand?`, `issuer_country?` |
| Payment attempt | `payment_id`, `invoice_id?`, `payment_method_id`, `psp`, `amount_minor`, `currency`, `attempt_no`, `context` |
| Refund | `refund_id`, `payment_id`, `amount_minor`, `reason` |
| PSP | `psp` code (reference data) |

## Event envelope

Every event, of any type, shares one envelope:

| Field | Meaning |
|---|---|
| `event_id` | Unique, idempotency key |
| `event_type` | From the closed vocabulary below |
| `schema_version` | Envelope/payload version |
| `occurred_at` | When it happened (UTC) |
| `ingested_at` | When the system recorded it (UTC) |
| `merchant_id` | Tenant scope |
| `entity_id` | ID of the primary entity (payment attempt, invoice, subscription, refund, …) |
| links | `customer_id`, `payment_intent_id` (explicit `unknown` when not applicable) |
| dimensions | `customer_country`, `issuer_country` (country is always named), `currency`, `psp`, `payment_method_type`, `card_brand`, `platform`, `app_version`, `plan_id`, `channel` (explicit `unknown` when not applicable), `attempt_no` (integer; `0` = not an attempt event) |
| measures | `amount_minor`, `currency` where relevant |
| outcome | `status`, `decline_code?`, `error_code?`, `idempotency_key_present`, `latency_ms?` (not produced yet) |
| `run_id` | Synthetic run identifier; partitions the table (**never** exposed to detection or AI; INV-015). Named `scenario_id` before ADR-025; `scenario_id` now means a ground-truth record only. |
| `raw_event_id`, `raw_seq` | Trace back to the raw layer (`events`) |

The physical table is `<db>.events_norm`, built by a pure, versioned mapping from the raw layer (ADR-022 A, ADR-025; `NORMALIZATION_VERSION`).

## Event types (initial vocabulary)

`checkout.started` · `payment.attempted` · `payment.authorized` · `payment.declined` ·
`payment.failed` (technical) · `payment.captured` · `invoice.created` · `invoice.paid` ·
`invoice.payment_failed` · `subscription.renewal_attempted` · `subscription.renewed` ·
`subscription.past_due` · `dunning.attempted` · `subscription.canceled` ·
`refund.requested` · `refund.succeeded` · `chargeback.opened` · `fraud.flagged`

`payment.attempted` and `chargeback.opened` are in the vocabulary but are not produced yet (ADR-025). Changes to this vocabulary are versioned and recorded in `DECISIONS.md`.

## Cohort dimensions

The dimensions the cohort engine may decompose by:
`psp`, `payment_method_type`, `card_brand`, `country` (issuer or customer — must be named
explicitly), `currency`, `platform`, `app_version`, `plan_id`, `merchant_id`, `attempt_no`.

## Derived analytical objects (contracts)

| Object | Produced by | Contents |
|---|---|---|
| Metric point | Metrics layer | window, dimensions, numerator, denominator, value, metric name + version |
| AnomalySignal | Detection | metric, scope, window, observed, expected, effect size, strength, detector + version |
| IncidentCandidate | Cohort engine | signals, ranked cohorts with contributions, affected volume, impact estimate |
| JevState | Code (from IncidentCandidate) | small named semantic fields and buckets; no raw events, no raw timestamps for comparison |
| Assessment | Code, from Jev's typed answers | incident probability, cause distribution, per-cause plausibility, evidence scores, missing evidence, confidences, question-set + model version |
| Incident | Incident engine | state, severity, impact, linked candidates/assessments/evidence, audit log |

## Cause vocabulary (closed, versioned)

Jev picks among causes; it cannot invent one. Ground truth `true_cause` uses the same list,
so hypothesis accuracy is measured on identical labels. Initial set (v1):

`psp_degradation` · `payment_method_degradation` · `issuer_or_country_degradation` ·
`checkout_regression` · `renewal_job_failure` · `dunning_failure` · `refund_process_change` ·
`duplicate_charging` · `fraud_attack` · `pricing_or_plan_change` · `data_pipeline_issue` ·
`normal_variation` · `unknown`

Changes to the vocabulary bump its version and are recorded in `DECISIONS.md`. Jev's
`Choice` has a hard limit of 255 options; the vocabulary must stay far below that.

## Severity vocabulary (closed, ADR-028)

`none` · `low` · `medium` · `high` · `critical` (the same labels as ground-truth `severity`). Jev ranks `low…critical`; `none` means "not an incident". UI labels such as `P1/P2/P3` are a presentation mapping only (Stage 8).

## Routes

`INCIDENT` ↔ ground-truth `incident`, `DIGEST` ↔ `watch`, `IGNORE` ↔ `suppress` (ADR-028).

## Semantic encoding for Jev

Jev is weak at numeric precision, counting, and date comparison. Code therefore converts
numbers and times into **named buckets** before they enter a `JevState`, with bucket edges
defined and versioned in code, for example:

| Quantity | Example buckets |
|---|---|
| Relative change of a rate | `none` · `slight` · `moderate` · `severe` · `collapse` |
| Statistical strength | `weak` · `moderate` · `strong` |
| Onset | `in_last_15m` · `in_last_hour` · `in_last_6h` · `earlier` |
| Share of change explained by top cohort | `minor` · `partial` · `most` · `nearly_all` |
| Alignment with a deploy | `none_nearby` · `after_deploy` · `before_deploy` |

Exact values stay in the `IncidentCandidate` and the audit record, so every bucket is traceable.

## Ground truth (synthetic scenarios)

Each scenario run produces a **ground-truth record stored separately from the events**, so
that no detection or AI component can read it:

| Field | Meaning |
|---|---|
| `scenario_id`, `scenario_type`, `seed` | Identity and reproducibility |
| `is_incident` | False for normal-variation scenarios |
| `onset_at`, `end_at?`, `ramp` | Timing and shape (step, gradual, recovering) |
| `affected_cohorts` | Exact dimension values that were perturbed |
| `affected_metrics` | Which metrics the perturbation should move |
| `injected_effect` | Magnitude per metric/cohort |
| `true_cause` | Label from a closed cause vocabulary |
| `true_impact` | Lost revenue (minor units) and affected payment count |

Invariant: ground truth is derived from the scenario definition, not from the generated
data, so evaluation cannot be circular.
