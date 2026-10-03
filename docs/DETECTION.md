# Detection (Stage 3)

Answers **"what changed?"** deterministically on the metric series of Stage 2 — no severity, no cause, no incident.
Output: `AnomalyCandidate`s for Stage 4 (cohorts) and Stage 5 (Jev). Code: `src/anomalyos/detection/`.
Decisions: ADR-034 (design), ADR-035 (Gate 1 results, frozen parameters). Ground truth is never read (INV-015).

## Monitored series

| Key | Metric (version) | Scope | Grain | Direction | Notes |
|---|---|---|---|---|---|
| S1 | `authorization_rate` | global | 15 min | down | |
| S2 | `authorization_rate` | psp | 1 h | down | |
| S3 | `authorization_rate` | customer_country | 1 d | down | + CUSUM (slow drift) |
| S3h | `authorization_rate` | customer_country | 1 h | down | Gate 1: short country outages |
| S4a / S4b | `checkout_conversion_rate` (v2, first look) | global / platform | 1 h | down | |
| S5 | `renewal_success_rate` | psp | 1 d | down | + CUSUM |
| S5b | `dunning_recovery_rate` | psp | 1 d | down | + CUSUM; Gate 1 |
| S6 | `refund_count` | customer_country | 1 d | up | + CUSUM; Gate 1 (count, not ratio) |
| S7 | `duplicate_charge_rate` | global | 1 h | up | |
| S8 | `attempt_volume` | psp | 1 h | both | |
| S9 | `fraud_flag_rate` | global | 1 h | up | |
| S10 | `subscription_cancellation_rate` (v2, voluntary only) | customer_country | 1 d | up | + CUSUM; Gate 1 |
| S11 | `late_arrival_share` | global | 1 h | up | windowed by delivery time |

All series are computed once per world with **first-look visibility** (a window sees only events delivered by its
close) and scanned sequentially, which is equivalent to deciding at every window close. A test proves that
truncating a series never changes earlier decisions.

## Method

1. **Baseline:** same slot of days d−8 … d−2 (the last day skipped so a fresh incident cannot enter its own
   baseline); the most extreme reference dropped when ≥ 4 remain. No decision in the first 3 days (warm-up).
2. **Standardized deviation** on variance-stabilized scales (arcsine for proportions, Anscombe for counts) with
   the baseline's own estimation variance; the plain normal z paged on stationary noise (binomial skew).
3. **Overdispersion:** φ = (median |z| / 0.6745)² over the last 168 unit-variance z, floored at 1 (default 2 with
   fewer than 6 values).
4. **Gates:** ≥ 30 attempts (rates), ≥ 5 expected (counts), ≥ 3 events of excess.
5. **Rules:** z ≥ 4 in one window, or z ≥ 2.5 in 2 of the last 3; CUSUM (k = 0.5, h = 4) on daily series;
   recovery after 2 consecutive windows with z below 1 in the alarm direction.
6. **Candidate lifecycle and prefilter:** one open candidate per series and scope (adjacent windows and both
   detectors merged); `promoted` / `suppressed` (reason) / `recovered`; sub-scope candidates linked to an
   overlapping global one (`parent_id`). Spec 04 items without a data source (health checks, probes, maintenance)
   are not applicable yet.
7. **Static-threshold system** (comparison, spec 10): fixed band around the warm-up median, single-window firing.

Parameters (frozen, ADR-035): `DetectorConfig()` in `src/anomalyos/detection/config.py`.

## Evaluation rules (fixed before results; ADR-034 D-7, Gate 1 corrections)

- Match = time overlap with `[start, max(end, expected_recovery)]` **and** `detected_at ≥ start` **and** compatible
  scope **and** the candidate metric evidences one of the record's `affected_metrics`.
- During a `data_pipeline_issue`, any first-look metric of the delayed cohort is a symptom of the delay.
- Recall over incidents with `oracle_detectable_at` set; misses split into warm-up and other.
- Latency = `detected_at` − `max(start, oracle_detectable_at)` (negative = earlier than the oracle).
- False positives: no match; matches only `suppress` records; or a metric the record declares unchanged.
  `watch` hits are reported only.

Code: `src/anomalyos/evaluation/detection.py`; runner: `scripts/eval_detection_dev.py` (DEV seeds only; refuses
HELDOUT seeds; writes `reports/`, not committed).

## Results on DEV seeds (20 seeds × realism v1/v2, scale 1.0, randomized calendar)

| Run | Change | Main recall v1 / v2 | Main FP/day | Static recall v1 / v2 | Static FP/day |
|---|---|---|---|---|---|
| 1 | design as approved | 0.78 / 0.77 (inflated, see ADR-035) | 1.06 | 0.74 / 0.77 | 4.2–4.4 |
| 2 | A: `detected_at ≥ start`; B: S3h, S5b, S10 v2; C: refund_rate not "unchanged" for approval incidents | 0.88 / 0.86 | 0.79 | 0.84 / 0.84 | 6.5–8.0 |
| **3** | S6 as refund count; pipeline symptoms; regression expectation | **0.89 / 0.87** | **0.53** | 0.83 / 0.83 | 6.2–7.6 |

Run 3, main system: latency median 1 h, p90 16 h (v1) / 19 h (v2). Perfect or near-perfect: checkout regression,
duplicates, gradual drift, payment-method outages, simultaneous incidents, renewal failures, data pipeline (19/20),
card testing (19/20), price change (18/19), dunning (17–18/20).

## Known limitations

| Limitation | Evidence (run 3) | Where it is addressed |
|---|---|---|
| Refund spikes | 10/20 | Better refund signal needed (hourly counts are below the sample floor) |
| Cohort-level changes (mix-shift masking, short country × PSP outages, recoveries) | 9–11/19, 13–14/20, 14–15/20 | Stage 4 (cohort decomposition) |
| Warm-up | 9 incidents in days 0–2 undetectable | Inherent to a lagged baseline |
| Healthy volume changes (holiday, campaign, promotion) | 100 candidates, counted as false positives | Stage 5–6 (Jev, policy) |
| Conversion by platform | ~100 false positives (small android cohort) | Candidate for a coarser grain or a higher floor |
| Latency p90 16–19 h | daily series for dunning, cancellations, refunds, renewals | Inherent to their volume |
