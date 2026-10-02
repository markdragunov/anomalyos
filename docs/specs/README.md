> **Status:** build specs, aligned with the accepted ADRs (019, 022, 024, 026, 027) in Phase 3 of the consolidation closeout. Where a spec still conflicts with `docs/DECISIONS.md`, the ADRs win. Stage/branch/status table: [`STATUS.md`](STATUS.md).

# AnomalyOS v2 — Claude Opus 5.5 Build Specs

These 13 Markdown files are the revised build specification for AnomalyOS.

They incorporate the architectural lessons from:
- Muhamad Talebi's JevOps article
- the `Mu99Ti/Jevops` repository
- the existing AnomalyOS Stripe-shaped billing design

Core principle:

> **Jev decides. Code routes. The LLM explains.**

## What changed from v1

The old design was mostly a linear pipeline:

```text
analytics → anomaly → cohorts → Jev → incident → agent → UI
```

The new design is explicitly **two-speed**:

```text
                    ClickHouse
                        ↓
              deterministic analytics
                        ↓
             ┌──────────┴──────────┐
             ↓                     ↓
        MODE A: TRIAGE        MODE B: INVESTIGATE
             ↓                     ↓
          prefilter              chunking
             ↓                     ↓
        burst ranking         Jev drilldown
             ↓                     ↓
            Jev                evidence
             ↓                     ↓
         verifier              agent
             ↓                     ↓
          policy                 Jev
       ┌────┼────┐               ↓
    IGNORE DIGEST INCIDENT       LLM
                                  ↓
                              explanation
```

## Files (13 including `STATUS.md`)

1. `00_MASTER_CONTEXT.md`
2. `01_FOUNDATION.md`
3. `02_BILLING_SIMULATION.md`
4. `03_CLICKHOUSE_ANALYTICS.md`
5. `04_ANOMALY_DETECTION.md`
6. `05_COHORT_INTELLIGENCE.md`
7. `06_JEV_INTELLIGENCE.md`
8. `07_INCIDENT_ENGINE.md`
9. `08_INVESTIGATION_AGENT.md`
10. `09_INCIDENT_UI.md`
11. `10_EVALUATION_BENCHMARK.md`
12. `11_FINAL_REVIEW.md`
13. this README (plus `STATUS.md`)

## Recommended Cursor workflow

For every stage:

1. Open the stage file.
2. Send it to Claude Opus 5.5.
3. Ask Claude to audit the repository before coding.
4. Review the implementation plan.
5. Implement only the requested stage.
6. Run tests, lint and type checks.
7. Inspect the diff.
8. Commit.
9. Move to the next stage.

Stage, branch and status of each file: [`STATUS.md`](STATUS.md) (the commit list that used to be here did not match repository stages).

## Stripe-shaped model

The simulation uses:

```text
Customer → PaymentMethod
Customer → PaymentIntent → Charge → Refund
Customer → Subscription → Invoice → PaymentIntent
Event wraps object state changes
```

This is Stripe-inspired, not an official Stripe API implementation.

## Important implementation rules

- AI never receives unbounded raw event streams.
- Jev never writes prose.
- Jev never executes SQL.
- Jev never executes tools.
- Policy lives in deterministic code.
- LLM explanation happens after evidence narrowing.
- Agent tools are typed and read-only.
- Evidence IDs are validated.
- Investigation budgets are enforced.
- Ground truth is independent of AI.
- Benchmarks must be able to disprove the hypothesis.

## Final product framing

> **AnomalyOS is an AI Incident Intelligence system for Billing & Payments that separates high-volume typed decision-making from slower investigation and explanation.**

The strongest demo should make the architecture visible:

```text
1M synthetic billing events
        ↓
statistical filtering
        ↓
candidate anomalies
        ↓
Jev decision layer
        ↓
IGNORE / DIGEST / INCIDENT
        ↓
bounded investigation
        ↓
Jev hypothesis decisions
        ↓
evidence-backed LLM explanation
```
