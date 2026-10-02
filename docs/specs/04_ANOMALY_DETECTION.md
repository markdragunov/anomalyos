# Stage 3 — Statistical Candidate Detection

> **Status:** pending Stage 2 (`STATUS.md`). Amended per the specs review and ADR-026; the pipeline below is unchanged.

## Objective

Detect unusual billing/payment behavior without AI.

This layer answers:
> What changed?

It must not decide:
> Is this an incident?

## Detection pipeline

```text
metric
 ↓
baseline
 ↓
deviation
 ↓
sample-size gate
 ↓
persistence/change-point gate
 ↓
candidate anomaly
```

Implement:
1. absolute threshold
2. rolling baseline
3. z-score/standardized deviation
4. minimum sample size
5. persistence
6. recovery detection

Avoid future leakage.

## Candidate object

```text
anomaly_id
metric
window
observed
expected
delta
relative_delta
statistical_score
sample_size
method
dimensions
evidence_ids
status
```

Status should be one of:
- candidate
- suppressed
- recovered
- promoted

Do not use P1/P2/P3 here.

## Mode A prefilter

Implement a cheap prefilter before Jev:
- known health checks
- probes
- heartbeats
- expected maintenance noise
- low-volume candidates
- duplicate candidate suppression

The prefilter must be deterministic and explainable.

## Burst ranking

Create a bounded candidate ranking stage inspired by JevOps.

Group nearby anomalies into a bounded burst/window.

The ranking input should be compact:
- anomaly IDs
- metric summaries
- impact proxies
- persistence
- affected volume

Do not send raw events.

The burst ranker may use Jev later; this stage only defines the interface.

## Acceptance

- seeded incidents become candidates
- normal seasonality does not automatically become an incident
- small samples are gated
- recovery is represented
- candidate volume can be measured before/after prefilter

## Amendments (from review and ADR-026)

- **Grain and floor.** Series at global 15 min, PSP 1 h, plus daily for slow drift (ADR-026). No standalone detection series for finer cohorts; the sample-size gate is ≥ 30 attempts, below which a candidate is `suppressed` with reason `insufficient_data`.
- **Baseline must not absorb drift.** A rolling baseline that follows the series hides slow degradation (gradual scenario: ES × psp_gamma over 7 days). Use a frozen or lagged baseline (reference period that ends before the candidate window) and compare against it; report both.
- **Seasonality.** The world has diurnal, weekly, payday and month-end shape; the baseline is conditioned on hour-of-week (and the month-end dip), not a flat mean.
- **Harmless seasonality.** `harmless_seasonality` (BR demand ×2.2) moves global approval only through *mix shift*; the detector must label it as explained variation. It is a required negative test, together with `normal_variation`.
- **Detectability.** Measure detection latency against `oracle_detectable_at` in ground truth once it exists (`SIMULATOR-FIXES`), not against scenario start. For ramps the start is zero intensity.
- **Isolation.** The detector never reads ground truth (see spec 03).
