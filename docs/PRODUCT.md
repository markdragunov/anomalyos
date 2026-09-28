# Product

AnomalyOS is **AI Incident Intelligence for Billing & Payments**.

This file is product intent for coding agents. It is **not** a license to implement Stage 0 or any runtime. Implementation waits for an explicit Stage 0 request.

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

## Out of scope for this repository stage

- Event ingestion and schemas
- ClickHouse deployment
- Detectors, Jev implementation, policy rulesets
- Investigation agent runtime
- Operator UI
- Authentication, multi-tenant control plane, production remediations

Those belong to later stages, each of which must land with architecture tests and eval ground truth — not as drive-by files during harness work.
