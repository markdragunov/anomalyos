# Stage 3 — design note (Gate 0)

Status: **proposed**, awaiting owner approval. No detection code exists yet. Decisions marked **D-n** become ADRs after approval.

## 1. Monitored series (D-1)

| # | Metric | Scope (group_by) | Grain | Direction | Main scenarios it should catch |
|---|---|---|---|---|---|
| S1 | `authorization_rate` | global | 15 min | down | large PSP / method / country outages |
| S2 | `authorization_rate` | psp | 1 h | down | PSP degradation, recovery, simultaneous, method outages (routed to one PSP) |
| S3 | `authorization_rate` | customer_country | 1 d | down | slow drift (gradual), country degradation, masked drops |
| S4 | `checkout_conversion_rate` | global, platform | 1 h | down | checkout regression (app version is a Stage 4 drill-down) |
| S5 | `renewal_success_rate` | psp | 1 d | down | renewal failure, dunning failure |
| S6 | `refund_rate` | customer_country | 1 d | up | refund spike |
| S7 | `duplicate_charge_rate` | global | 1 h | up | duplicate charging |
| S8 | `attempt_volume` | psp | 1 h | up and down | card testing, outages that stop traffic |
| S9 | `fraud_flag_rate` | global | 1 h | up | card testing |

Not monitored in Stage 3: country × PSP and deeper (pooled in Stage 4, ADR-026); cancellations and ingestion lag
(no metric yet: `subscription_cancellation_rate` and an ingestion-lag metric are added to the metrics layer here,
v1, so S10 `cancellation_rate` by country, 1 d, up, and S11 `ingestion_lag_share` global, 1 h, up, exist).

## 2. Baseline (D-2)

Expected value of window *w* = pooled numerator / pooled denominator of the **same hour-of-day** (or the same weekday
for 1 d) over a **lagged reference**: days *d−8 … d−2* (skips the last day so a fresh incident does not enter its
own baseline), **trimmed** (drop the reference day with the most extreme rate) so one past incident does not move it.
A slow drift is compared with values 2–8 days old, so it cannot hide in its own baseline.
Warm-up: no candidates in the first 3 days (fewer than 2 reference days); the world's first week has a wider band
(fewer reference days → larger variance term).

## 3. Detectors (D-3)

| Detector | Rule | Parameters (initial, tuned on DEV only) |
|---|---|---|
| **T** static threshold (comparison baseline, spec 10) | rate below a fixed floor / count above a fixed ceiling | floor per metric from DEV-seed quantiles |
| **Z** standardized deviation | z = (obs − exp) / sqrt(n·p·(1−p)·φ); counts: (obs − exp) / sqrt(exp·φ) | φ = Pearson dispersion of the scope's reference windows, floored at 1 (v2 has φ ≈ 1.5–2) |
| **N** minimum sample | denominator < 30 → no z, status `suppressed`, reason `insufficient_data` | 30 (ADR-026) |
| **P** persistence | candidate if z ≤ −4 in one window, or z ≤ −2.5 in 2 of the last 3 windows | z1 = 4, z2 = 2.5, k/n = 2/3 |
| **C** CUSUM for drift | one-sided CUSUM on standardized residuals (1 d series), reset after an alarm | k = 0.5, h = 4 |
| **R** recovery | open candidate → `recovered` after 2 consecutive windows with z > −1 | 2 windows |

Each detector is a pure function over a dense series (Stage 2 `SeriesPoint`s) plus the baseline; ClickHouse is
reached only through `metrics.compute` (INV-003).

## 4. Candidate object and statuses (D-4)

Fields as spec 04: `anomaly_id` (stable hash of metric, version, scope, first window, detector — identical on
replay), `metric`, `metric_version`, `scope` (dims), `window_start`, `window_end`, `observed`, `expected`, `delta`,
`relative_delta`, `score` (z or CUSUM), `sample_size`, `method`, `evidence_ids` (one per metric query + window),
`status`, `status_reason`, `detected_at` (the `as_of` at which it first appeared).
Transitions: `candidate → promoted` (passes the prefilter: handed to Stage 4/5), `candidate → suppressed`
(prefilter reason), `promoted → recovered` (detector R). No severity, no cause.

## 5. Mode A prefilter (D-5)

Deterministic, each decision carries a reason: low volume (N); duplicate suppression (same metric and scope,
overlapping windows → one candidate, methods merged); adjacent windows merged into one candidate; a candidate on a
scope whose parent scope already has an open candidate for the same metric is linked, not duplicated.
Not applicable yet (no data source in the simulator): health checks, probes, heartbeats, maintenance windows.

## 6. Replay and visibility (D-6)

Detection decides once per window, **at window close**, with the data visible then: events with
`occurred_at < window_end` **and** `ingested_at <= window_end` ("first look"). Late events from
`data_pipeline_issue` therefore lower the first-look value of their window; that dip is what S11 / the
`data_pipeline_issue` incident is about. This needs one addition to the metrics layer: an optional
`visibility="window_close"` predicate (`ingested_at <= window_end`, computed in SQL, one query per series —
no per-`as_of` re-query loop). `detected_at` = the closing time of the window that produced the candidate.

## 7. Evaluation rules (D-7) — fixed before any result is seen

- **Match** candidate ↔ ground-truth record when all hold: (a) time: candidate window overlaps
  `[start, max(end, expected_recovery)]` (open-ended records: up to world end; refund records: `end + 6 h`);
  (b) scope: global always compatible; a `psp` / `customer_country` / `platform` scope is compatible if the
  record's affected cohort has the same value for that dimension or no constraint on it; (c) metric: the
  candidate metric maps to one of the record's `affected_metrics` (mapping table in `evaluation/detection.py`).
- **Recall** = detected / incidents with `oracle_detectable_at != null` (ADR-031). Records with a null oracle are
  counted separately. `watch` records are reported, not scored.
- **Latency** = `detected_at` of the first matching candidate − `max(start, oracle_detectable_at)`; can be negative
  only if the detector fires before the oracle (reported, not clipped).
- **False positives**: promoted candidates matching no record, per world-day. Candidates matching only `suppress`
  records are reported per kind (seasonality, benign shock, campaign, tiny cohort, promotion) and **count as false
  positives**. A candidate on a metric listed in a matched record's `unchanged_metrics` counts as a false positive.
- Several candidates on one record: the record counts once; the extra matching candidates are not false positives.

## 8. Seeds, cost, where it runs (D-8)

Tuning only on `DEV_SEEDS` (1–20), randomized schedule, scale 1.0, realism v1 **and** v2. `HELDOUT_SEEDS` untouched.
Per seed and realism: generation ≈ 35 s, embedded ClickHouse load ≈ 45 s, normalization ≈ 25 s, detection a few
seconds → about 2 min; 40 worlds ≈ 80 min on this machine, in the background. CI runs only unit tests and one
small end-to-end world (scale 0.1) so it stays under a few minutes.

## Open questions for the owner

1. Should candidates matching `suppress` records count as false positives (proposed: yes, they are what Mode A
   must not page on), or only be reported?
2. Is first-look visibility (D-6) the right semantics for late data, or should detection also re-evaluate a window
   when late events arrive (more realistic, more complex)?
3. Add the two missing metrics (cancellation rate, ingestion-lag share) in this stage (proposed) or defer
   `pricing_or_plan_change` and `data_pipeline_issue` detection to a later stage?
