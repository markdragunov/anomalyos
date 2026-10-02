# Product

AnomalyOS is **AI Incident Intelligence for Billing & Payments**.

This file is product intent for coding agents. Implementation proceeds one stage at a time (`docs/ARCHITECTURE.md` → *Stage status*); a stage starts only when its task brief in `docs/tasks/` is requested.

## Problem

Billing and payments failures hide in high-volume event streams: processor outages, rail degradation, auth-rate cliffs, decline-code shifts, settlement mismatches, webhook delay, and cohort-specific breakage (one method, one geo, one merchant tier).

Humans cannot read the firehose. Unbounded LLMs must not read it either (`INV-001`). Operators need **dispositions** they can trust: ignore noise, digest what matters, open incidents when a cohort is actually hurting — then **investigate** with evidence, not vibes.

## Principle

**Jev decides. Code routes. LLM explains. Agent investigates.**

- Detection and cohorting are **code**.
- Disposition is **Jev + deterministic policy**, not a chatbot.
- Investigation is a **budgeted, read-only (V2)** agent.
- Prose is an **explanation over validated evidence IDs**.

## Users (V2)

Primary: payments / billing operators and on-call engineers who need to know *what broke, for whom, how sure we are, and what evidence supports that*.

Not in V2: autonomous refunds, processor cutovers, or customer-facing chat.

## Dispositions

| Disposition | Meaning |
| --- | --- |
| `IGNORE` | Candidate is noise or below policy; keep an audit record. |
| `DIGEST` | Notable, not a page; include in a periodic or on-demand digest. |
| `INCIDENT` | Actionable cohort impact; enter Mode B investigation. |

Policy owns the enum. The LLM does not.

## Modes

- **Mode A — Detection and disposition.** Aggregates → anomalies → cohorts → Jev → policy → enum. Replayable.
- **Mode B — Investigation and explanation.** Read-only evidence gathering, Jev re-evaluation, LLM explanation citing evidence IDs. No irreversible actions.

## Analytical truth

ClickHouse will be the analytical source of truth. Product questions (“auth rate for card-not-present in DE vs 7-day baseline”) are answered through **approved typed interfaces**, never arbitrary SQL and never raw event dumps into a prompt.

## Epistemic honesty

Every figure and claim is **observed**, **inferred**, **estimated**, or **recommended**. Mixing these is a product defect, not a copy issue.

## Not yet built

See *Stage status* in `docs/ARCHITECTURE.md`. Authentication, multi-tenant control plane and production remediations are out of scope for the prototype.

---

# Product detail (prototype scope)

### Problem

Billing and payment failures are expensive and quiet. A PSP degrades for one card brand in
one country, a renewal job silently skips a plan, a new app version breaks 3DS. Blended
dashboards stay green because the failing slice is small relative to total volume. By the
time finance or support notices, the loss has compounded, and the on-call engineer starts
from zero: *what changed, where, since when, how much, why?*

Generic observability tools answer "is the service up?". They do not answer "is revenue
leaking, for whom, and why?".

### What AnomalyOS is

An incident-intelligence system for billing and payments. It:

1. **Detects** statistically meaningful changes in business metrics (deterministic).
2. **Localizes** them to the cohorts that explain the change (deterministic).
3. **Judges** whether the change is likely a real incident and which hypotheses fit (AI, structured).
4. **Supports investigation** by proposing the next evidence to gather (AI agent, read-only).
5. **Leaves the decision to a human**, with an auditable trail from event to verdict.

### What it is not

- Not a generic observability or APM platform.
- Not an autonomous remediation system: it never retries payments, switches PSPs, refunds,
  or closes incidents on its own.
- Not a payment processor or integration hub. Data is synthetic until the hypothesis is validated.

### Users and jobs

| User | Job to be done |
|---|---|
| Payments / billing on-call | "Tell me quickly whether this is real, where it is, and what to check first." |
| Payments ops / PSP manager | "Show which PSP, method, or country is degrading and since when." |
| Finance / RevOps | "How much revenue is at risk or lost, with evidence I can trust?" |
| Product / engineering lead | "Did our release cause this, and which cohort proves it?" |

### Core loop

```
Events → Analytics → Anomaly Detection → Cohort Decomposition → Incident Candidate
      → Structured Reasoning → Incident → Investigation → Evidence → Human Decision
```

### Initial vertical and scenarios

Vertical: **Billing & Payments Incident Intelligence.** The architecture stays domain-agnostic
at the layer boundaries so other financial/operational domains can be added later.

Scenario catalogue (each gets explicit ground truth, see `DATA_MODEL.md`):

1. PSP degradation · 2. Payment-method degradation · 3. Country-specific degradation ·
4. Checkout regression · 5. Subscription renewal failure · 6. Dunning failure ·
7. Refund spike · 8. Duplicate charge anomaly · 9. Fraud spike · 10. Gradual degradation ·
11. False positive / normal variation · 12. Correlated anomalies · 13. Recovery after incident

Scenarios 11 and 13 matter as much as the incidents: a system that cannot stay quiet or
recognise recovery is not trustworthy.

### Success criteria (prototype)

Measured against synthetic ground truth by the evaluation layer:

| Metric | Meaning |
|---|---|
| Detection recall / precision per scenario | Real incidents found; normal variation left alone |
| Time to detect | Simulated time from incident onset to incident candidate |
| Cohort localization accuracy | The root cohort is ranked first (or top-k) |
| Hypothesis accuracy | The true cause (closed vocabulary) is Jev's top choice, or in its top-k |
| Impact estimate error | Estimated vs true lost revenue / affected payments |
| Calibration | Stated confidence matches observed accuracy |
| Auditability | Every AI output is traceable to its input state and version |

Target values are set per stage once a baseline exists; they are not invented up front.

### Authority model

| Decision | Owner |
|---|---|
| What counts as an anomaly (thresholds, methods) | Deterministic code + configuration |
| Whether a candidate is likely meaningful; hypothesis ranking | Jev (advisory, validated) |
| Incident lifecycle transitions | Incident engine (deterministic rules) |
| What to investigate next | Agent (advisory, read-only tools) |
| Resolve / close / dismiss an incident | **Human only** |
