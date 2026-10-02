> **Status:** target design, written before implementation. Where it conflicts with `docs/DECISIONS.md`, the ADRs win. Known conflicts: the "LLM explains" step vs ADR-018 (Jev generates no text; a generative model needs its own ADR), and who picks the investigation tool (spec 08: the agent; ADR-019, proposed: Jev from a closed set). See also ADR-024.

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

## 13 files

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
13. this README

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

Suggested commits:

```text
001 foundation
002 billing simulation
003 clickhouse analytics
004 anomaly detection
005 cohort intelligence
006 jev decision layer
007 incident routing
008 investigation agent
009 incident ui
010 benchmark
011 final review
```

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
