# Metrics

Canonical definitions of the metrics layer (`src/pulseos/metrics/registry.py`). One definition per
`(name, version)`; changing a definition bumps its version, never edits it. Every metric is computed
in ClickHouse over `events_norm` (ADR-022 A, ADR-025) and returns, per window and group,
`numerator`, `denominator` and `value` (`NULL` when the denominator is 0; never 0, never NaN).

## Contract

`compute(runner, database, run_id, metric, version, start, end, grain, filters, group_by) -> [SeriesPoint]`

- `start` is grain-aligned UTC; `end` is the as-of bound: only events with `occurred_at < end` are read. The last window is `is_complete = false` if it extends past `end`.
- Grains: `5m`, `15m`, `1h`, `1d` (UTC). Per-scope choice follows ADR-026: global 15 min, PSP 1 h, daily for slow drift; finer cohorts are evaluated pooled over a candidate window. The **sample floor (30 attempts) is not applied here**: this layer reports counts, the detection and cohort layers gate on them.
- `visibility="window_close"` keeps only rows with `ingested_at <= window end` (first look: late events are invisible until delivered; ADR-034). Default `all`.
- The series is **dense**: every window of every group seen in the data is present; a window with no source rows has numerator 0 and the metric's *empty denominator* (0 for ratios, 1 for counts and sums).
- `filters` / `group_by` accept only DATA_MODEL cohort dimensions that are allowed for the metric; anything else raises `MetricError` before any SQL is sent. Identifiers are validated; values are bound as query parameters.
- Attempt counts include retries, which are not independent trials; interval estimates must account for that (ADR-026).
- No ground truth is read (INV-015; test-enforced).

## Definitions (all version 1)

| Metric | Numerator | Denominator | Notes |
|---|---|---|---|
| `authorization_rate` | `payment.authorized` | `payment.authorized` + `payment.declined` + `payment.failed` | per attempt (retries count) |
| `first_attempt_authorization_rate` | same, `attempt_no = 1` | same, `attempt_no = 1` | |
| `technical_failure_rate` | `payment.failed` | all attempts | `processing_error` |
| `checkout_conversion_rate` | `checkout.started` whose PaymentIntent has a `payment.authorized` before `end` | `checkout.started` | windowed by checkout start; recent windows are incomplete by nature; `payment_method_type`, `card_brand`, `issuer_country`, `attempt_no` are unknown at checkout start and not allowed |
| `renewal_success_rate` | `subscription.renewed` | `subscription.renewal_attempted` | each event counted in its own window; renewals complete after a lag (dunning), so the numerator trails; one attempt per billing cycle, not per charge |
| `refund_rate` | `refund.succeeded` | `payment.captured` | same window; refunds lag captures (1-10 days), so the ratio is only comparable between equally old windows |
| `duplicate_charge_rate` | `payment.captured` (checkout) without idempotency key that follows a same-customer, same-amount, same-currency capture within 60 s | `payment.captured` (checkout) | lookback of 60 s before `start` is read so a window start does not hide a duplicate; renewals excluded (they never carry a key); `channel` filter not available |
| `fraud_flag_rate` | `fraud.flagged` | all attempts | only `highest_risk_level` declines are flagged (ADR-025) |
| `attempt_volume` | number of attempts | 1 | denominator is the constant 1 |
| `checkout_conversion_rate` **v2** | `checkout.started` whose first authorization had been *delivered* by the end of the checkout's window | `checkout.started` | first look, no information from after the window closes; used by detection (ADR-034) |
| `subscription_cancellation_rate` | `subscription.canceled` | `subscription.renewal_attempted` | same window; can exceed 1 (cancellations after failed dunning come days after the attempt) |
| `late_arrival_share` | events delivered more than 15 min after they occurred | events delivered | **windowed by delivery time** (`ingested_at`), so it is known when the window closes |
| `subscription_cancellation_rate` **v2** | voluntary `subscription.canceled` (`canceled_voluntary`) | `subscription.renewal_attempted` + voluntary cancellations | excludes cancellations after failed dunning (ADR-035) |
| `dunning_recovery_rate` | renewal retries (`attempt_no > 1`) that authorize | `dunning.attempted` | counted at retry time |
| `refund_count` | `refund.succeeded` | 1 | not moved by this window's captures (ADR-035) |
| `revenue_collected_minor` | sum of `amount_minor` of `payment.captured` | 1 | always grouped by `currency` (no cross-currency sums) |

## Dimensions

`psp`, `payment_method_type`, `card_brand`, `customer_country`, `issuer_country`, `currency`, `platform`, `app_version`, `plan_id`, `channel`, `merchant_id`, `attempt_no`. Which of them a metric accepts is part of its definition (e.g. renewal metrics: `psp`, `customer_country`, `currency`, `plan_id`, `channel`, `merchant_id`).

## Tests

`tests/unit/metrics/`: every metric against an independent Python reference on a hand-built stream (zero-denominator window, incomplete trailing window, events at or after `end`, 60 s / 61 s duplicate boundary, lookback before `start`); validation and injection tests; sanity on simulated data (PSP degradation, Android 5.14.0 regression, renewal failure on `psp_gamma`). The SQL runs on a live ClickHouse when `PULSEOS_RUN_INTEGRATION=1` (CI), else on embedded `chdb`, else the tests skip with a stated reason.
