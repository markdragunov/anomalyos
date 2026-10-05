# PulseOS v2 — Master Context for Claude Opus 5.5

> **Status:** active; aligned with the accepted ADRs (see `STATUS.md`). Where this text conflicts with `docs/DECISIONS.md`, the ADRs win:
> - "The LLM explains" → an *explainer behind a port*, templated first; a generative model only later (ADR-027). Jev itself writes no text (ADR-018).
> - Jev is the hosted TypeSafe System One model reached through a `JevClient` transport adapter with replay; the decision contract stays pure (ADR-024, `INV-004`).
> - In Mode B the next step is chosen by Jev from a bounded shortlist built by code (ADR-019, option 3), not by a free-running agent.
> - Event contract: raw Stripe-shaped layer plus normalized DATA_MODEL layer (ADR-022 A). Metrics and detection read only the normalized layer.
> - Build order, stages and branches: `STATUS.md`.

You are the principal engineer and AI systems architect working on PulseOS.

Build PulseOS as a production-quality research prototype for **AI-native Billing & Payments Incident Intelligence**.

This specification has been revised after studying the JevOps architecture:
- Mode A: high-volume real-time triage
- Mode B: bounded hierarchical investigation
- typed Jev decisions
- policy in deterministic code
- LLM downstream for explanation
- bounded work, verifiable evidence, honest degradation

Reference architecture:
> **Jev decides. Code routes. The LLM explains.**

Do not copy JevOps literally. Adapt its architectural pattern to billing/payments and benchmark every important claim.

---

## 1. Product thesis

PulseOS should answer four progressively harder questions:

1. **What changed?** — deterministic analytics and anomaly detection.
2. **Does it matter?** — Jev decision layer.
3. **What should the system do with that decision?** — deterministic policy.
4. **Why did it happen / what should a human investigate next?** — bounded investigation + LLM explanation.

The product is not a generic dashboard and not an LLM chatbot.

Initial vertical:
> Billing & Payments Incident Intelligence

Primary operational objective:
> Reduce alert/incident noise while preserving meaningful business incidents.

---

## 2. Two-speed architecture

PulseOS has two explicit modes.

### Mode A — Real-time triage

Question:
> Should this candidate become an incident, a digest item, or be ignored?

Pipeline:

```text
Stripe-like events
      ↓
ClickHouse
      ↓
deterministic metric aggregation
      ↓
free/statistical prefilter
      ↓
candidate burst / anomaly ranking
      ↓
Jev typed decision fan-out
      ↓
deterministic verifier
      ↓
policy engine
      ├── IGNORE
      ├── DIGEST
      └── INCIDENT
```

Mode A must be cheap, bounded and safe.

### Mode B — Investigation

Question:
> What actually happened in this incident/time window, and what evidence supports the explanation?

Pipeline:

```text
incident / time window
      ↓
bounded chunks of analytical evidence
      ↓
Jev hierarchical importance decisions
      ↓
recursive drill-down
      ↓
small evidence set
      ↓
Investigation Agent
      ↓
typed read-only tools
      ↓
Jev hypothesis / evidence decisions
      ↓
LLM explanation
      ↓
validated incident report
```

The LLM must never become the uncontrolled selector of raw evidence.

---

## 3. Core responsibilities

### ClickHouse
Source of analytical truth.

Answers:
> What happened?

It performs aggregation, filtering, grouping and metric history.

### Anomaly Engine
Answers:
> What is unusual?

It is deterministic/statistical. It does not assign business severity or root cause.

### Cohort Engine
Answers:
> Where is the anomaly concentrated?

It performs controlled decomposition and identifies affected/control cohorts.

### Jev
Answers structured questions over compact state.

Examples:
- Is this meaningful?
- How likely is human attention required?
- Which category fits?
- Which hypothesis has the strongest support?
- Is evidence sufficient to stop investigating?
- Which chunk is most important?

Jev must not write prose or execute actions.

### Verifier
Checks schema, ranges, evidence references, consistency and required evidence.

### Policy Engine
Owns routing:
- ignore
- digest
- incident
- investigation stop/continue
- escalation eligibility

Policy is ordinary, inspectable code.

### Investigation Agent
Chooses the next bounded read-only investigation tool call.

It does not invent evidence.

### LLM
Explains validated evidence for humans. It may synthesize and summarize, but it cannot silently add facts, select arbitrary raw evidence, or change the incident state.

---

## 4. Decision contract

Use three Jev primitives where appropriate:

- `choice`: categorical decision
- `score`: ordered numeric/ordinal decision
- `noul`: yes/no likelihood

Example:

```json
{
  "is_incident": {"type": "noul"},
  "severity": {"type": "choice", "options": ["P3", "P2", "P1"]},
  "investigation_priority": {"type": "score", "min": 0, "max": 3}
}
```

Never treat Jev confidence as proof of correctness.

Every Jev result must be benchmarked and passed through deterministic verification.

---

## 5. Routing contract

Default conceptual routing:

```text
candidate
   ↓
Jev
   ↓
Verifier
   ↓
Policy
   ├── IGNORE
   ├── DIGEST
   └── INCIDENT
```

Illustrative policy only; thresholds must be configuration, not hard-coded assumptions.

Low confidence should be a first-class state:
> uncertainty becomes digest/hold rather than an alert.

---

## 6. Investigation principles

Use the JevOps Mode B pattern:

> Jev decides, code narrows, LLM explains.

For investigation:
- bounded chunk size
- bounded recursion depth
- bounded number of leaves
- bounded tool calls
- explicit stop conditions
- validated evidence references
- graceful degradation on model/tool failure

The agent must not repeatedly query the same metric without progress.

Maintain:
- hypotheses
- supporting evidence
- contradicting evidence
- unresolved questions
- next-best investigation action
- budget remaining

---

## 7. Stripe-inspired synthetic data model

Use independent synthetic implementations with Stripe-shaped semantics.

Objects:
- Event
- Customer
- PaymentMethod
- PaymentIntent
- Charge
- Refund
- Invoice
- Subscription

Relationships:

```text
Customer → PaymentMethod
Customer → PaymentIntent → Charge → Refund
Customer → Subscription → Invoice → PaymentIntent
Event wraps immutable object state changes
```

IDs:
`cus_`, `pm_`, `pi_`, `ch_`, `re_`, `in_`, `sub_`, `evt_`

Do not claim official Stripe API compatibility.

---

## 8. Non-negotiable engineering rules

1. Do not send raw event streams to AI.
2. Do not allow AI to generate arbitrary SQL.
3. Do not let Jev call tools.
4. Do not let Jev mutate state.
5. Do not let the LLM choose arbitrary evidence from the raw warehouse.
6. Do not let the agent change billing/payment configuration.
7. Do not let AI close incidents autonomously in MVP.
8. Every important decision is auditable.
9. Every scenario has explicit ground truth.
10. Every benchmark can disprove the hypothesis.
11. Every evidence item has a stable ID.
12. Observed, inferred, estimated and recommended information are separate.
13. All budgets are enforced in code.
14. Fail closed for irreversible actions.
15. Prefer vertical slices over premature frameworks.

---

## 9. Required audit record

Every Jev decision should be representable as:

```json
{
  "decision_id": "dec_123",
  "mode": "realtime_triage",
  "question": "is_incident",
  "input_state_hash": "...",
  "answer": true,
  "probability": 0.94,
  "confidence": 0.91,
  "evidence_ids": ["anom_123", "cohort_456"],
  "verifier_status": "accepted",
  "policy": "incident_policy_v1",
  "route": "INCIDENT"
}
```

Never store only the final answer; preserve enough information to reproduce the decision.

---

## 10. Evaluation philosophy

Do not claim Jev is superior.

Benchmark:
- rules
- statistical detection
- statistical + cohort
- statistical + Jev
- full investigation architecture
- where appropriate, frontier LLM baseline

Measure:
- precision/recall
- false positives
- detection latency
- localization
- evidence quality
- investigation efficiency
- calibration
- verifier rejection
- cost
- latency
- safety violations

The benchmark must be able to show Jev or the agent making things worse.

---

## 11. Build philosophy

Use Python, ClickHouse, FastAPI, Next.js/TypeScript and Docker Compose unless repository constraints justify otherwise.

Keep the core system deterministic and testable.

Build in this order:

1. foundation
2. Stripe-like billing simulation
3. ClickHouse analytics
4. anomaly detection
5. cohort intelligence
6. Jev decision layer + verifier
7. two-mode routing / incident engine
8. investigation agent
9. UI
10. evaluation
11. final review

The final system should be understandable by a senior payments/product/AI interviewer reading the repository.
