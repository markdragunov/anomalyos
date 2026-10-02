# Stage 9 — Evaluation and Benchmark

## Objective

Evaluate the entire architecture without assuming Jev or the agent helps.

The benchmark is a product-scientific instrument, not a demo.

## Experimental systems

Where technically valid, compare:

A. static threshold
B. statistical anomaly detector
C. statistical + cohort intelligence
D. statistical + cohort + Jev triage
E. full Mode A AnomalyOS
F. Mode B hierarchical Jev + LLM explanation
G. full investigation agent

Do not create unfair comparisons by giving one system information unavailable to another.

## Scenario matrix

Include:
- PSP degradation
- country degradation
- payment-method degradation
- app-version regression
- renewal failure
- refund spike
- duplicate charges
- fraud-like spike
- gradual degradation
- harmless seasonality
- tiny noisy cohort
- correlated unrelated anomalies
- simultaneous incidents
- recovery
- missing/contradictory evidence

Use many seeds.

## Mode A metrics

Detection:
- precision
- recall
- false-positive rate
- false-negative rate
- detection latency

Routing:
- incident precision
- digest rate
- noise reduction
- missed-incident rate
- verifier rejection rate

Operational:
- decisions/sec
- p50/p95 latency
- Jev calls
- estimated cost
- model failures

## Mode B metrics

Evidence narrowing:
- fraction of raw evidence exposed to LLM
- recall of ground-truth incident evidence
- number of Jev calls
- number of leaves
- latency
- cost

Explanation:
- citation precision
- citation recall
- unsupported-claim rate
- evidence completeness
- root-cause localization accuracy
- blast-radius accuracy

## Agent metrics

- investigation success
- correct hypothesis
- time to sufficient evidence
- tool-call count
- repeated-call rate
- premature conclusion rate
- contradiction handling
- budget compliance
- human escalation rate

## Calibration

Measure where possible:
- Brier score
- reliability curves
- expected calibration error
- confidence vs correctness

Do not assume vendor calibration claims transfer to billing.

## Critical benchmark rule

Ground truth comes only from the scenario engine.

Never:
- tune thresholds on test scenarios
- select only favorable seeds
- remove failures
- use future information
- let the agent see hidden ground truth
- use LLM output as ground truth

## Required benchmark report

Create:

```text
reports/benchmark.md
reports/benchmark.json
```

Include:
- methodology
- scenario matrix
- results
- confidence intervals where appropriate
- failures
- surprising results
- cost/latency
- limitations
- recommendation for next experiment

The report must be able to conclude that a simpler baseline is better.
