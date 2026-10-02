# Stage 3 — Statistical Candidate Detection

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
