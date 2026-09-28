# Architecture

This document describes system boundaries for AnomalyOS. The **runtime is documented, not implemented**. The **coding harness is implemented** in this repository.

## Two harnesses

### Coding harness (this repo, now)

Purpose: let Claude/Cursor (and humans) change AnomalyOS **safely and repeatedly**.

It enforces architecture, invariants, tests, evaluation structure, cost/latency trace shape, documentation, and git discipline.

Contents: `AGENTS.md`, `docs/`, `tests/architecture/`, `evals/`, `scripts/`, `.cursor/rules/`, `.github/workflows/`.

It is **not** the product. It does not ingest events, talk to ClickHouse, call an LLM, or open incidents.

### AnomalyOS runtime harness (documented, later)

Purpose: detect billing/payment anomalies, disposition them, investigate, and explain — with Jev as the only decision authority.

Documented flow:

```
Events
  → ClickHouse
  → Metric Aggregation
  → Anomaly Detection
  → Cohort Intelligence
  → Jev Decision Layer
  → Policy Engine
  → IGNORE | DIGEST | INCIDENT
  → Investigation Agent
  → Evidence
  → Jev
  → LLM Explanation
```

Principle: **Jev decides. Code routes. LLM explains. Agent investigates.**

Coding agents must not collapse these two harnesses (for example by putting a detector inside `scripts/` or calling an LLM from an architecture test).

## System boundaries

| Boundary | Inside | Outside |
| --- | --- | --- |
| Analytical truth | ClickHouse aggregates, approved typed reads | Raw payment-processor firehoses, LLM memory, ad-hoc SQL |
| Decision | Jev (pure) + deterministic policy | LLM “judgment”, agent improvisation |
| Investigation | Read-only approved fetches, evidence objects | Mutations, retries, refunds, config edits |
| Explanation | LLM over **validated** evidence IDs | Uncited narrative, hidden context dumps |
| Coding | This git repo, architecture tests, eval fixtures | Production data, customer PII in prompts |

## Major components (runtime, future)

1. **Event intake** — bounded, schema’d billing/payment events. Not an AI input.
2. **ClickHouse** — analytical source of truth. The only place aggregate metrics and cohort slices are computed for the product.
3. **Metric aggregation** — deterministic rollups (volume, conversion, auth rate, decline codes, latency, by processor/rail/merchant/geo/method).
4. **Anomaly detection** — code, not an LLM. Emits structured anomaly candidates with uncertainty, not prose.
5. **Cohort intelligence** — slices candidates into affected populations with sizes and comparators. Still code.
6. **Jev decision layer** — side-effect-free function. Consumes structured candidates + cohorts (+ later, evidence). Produces a decision record. **Cannot execute tools.**
7. **Policy engine** — deterministic mapping from Jev’s decision record + versioned rules → `IGNORE` / `DIGEST` / `INCIDENT`.
8. **Investigation agent** — V2 read-only. Budgeted. Builds an evidence bundle through approved interfaces.
9. **Evidence store** — objects with stable IDs and epistemic status (`observed` / `inferred` / `estimated` / `recommended`).
10. **LLM explanation** — last mile. May only cite validated evidence IDs. Must not change disposition.
11. **Audit log** — every important decision: inputs, policy version, evidence IDs, actor, budget consumption.

## Data flow

```
                    ┌─────────────────────────────────────────┐
  processors        │  CODING HARNESS (repo)                  │
  / billing ──┐     │  docs, invariants, tests, evals, CI     │
              │     └─────────────────────────────────────────┘
              ▼
        ClickHouse  (analytical SoT)
              │
              ▼
     Metric aggregation ──► Anomaly detection ──► Cohorts
                                                    │
                                                    ▼
                              ┌──────────┐    ┌──────────┐
                              │   Jev    │───►│  Policy  │
                              │ (pure)   │    │ (determ.)│
                              └──────────┘    └──────────┘
                                                    │
                                    IGNORE / DIGEST / INCIDENT
                                                    │
                                      (INCIDENT or drill-down)
                                                    ▼
                                        Investigation agent
                                           (read-only V2)
                                                    │
                                                    ▼
                                              Evidence[*id]
                                                    │
                                                    ▼
                                                  Jev
                                                    │
                                                    ▼
                                            LLM explanation
```

AI systems never sit on the event arrow. They sit on **typed, bounded, already-aggregated** arrows.

## Mode A — Detection and disposition

Automated path from aggregates to a disposition.

- Inputs: metrics, anomaly candidates, cohort slices (all structured).
- Decision: Jev, then policy.
- Outputs: `IGNORE`, `DIGEST`, or `INCIDENT` plus an audit record.
- **No** investigation tools. **No** LLM disposition. **No** raw events.

Mode A must be replayable: same stored aggregates + same policy version ⇒ same disposition.

## Mode B — Investigation and explanation

Entered after `INCIDENT` (or an explicit digest drill-down).

- Investigation agent gathers **read-only** evidence through approved interfaces, under explicit budgets.
- Evidence is stored with stable IDs and epistemic labels.
- Jev re-evaluates with the evidence bundle (still pure, still no tools).
- LLM writes an explanation that may only reference validated evidence.
- Policy may confirm or escalate; it remains deterministic.
- No irreversible action runs autonomously in V2.

## Roles (do not blur)

### Jev

- Pure decision function.
- Side-effect free. No I/O, no tools, no network, no clock-dependent luck in V2 (time may be an **input**, not something Jev samples).
- Outputs a structured decision record consumed by policy and audit.

### LLM

- Explains. Does not decide `IGNORE` / `DIGEST` / `INCIDENT`.
- Sees only validated evidence (and perhaps a Jev/policy record), never raw event streams or arbitrary warehouse dumps.
- Must not invent evidence IDs.

### Investigation agent

- Investigates. Does not disposition.
- V2: read-only, budgeted, no irreversible actions.
- Produces evidence objects, not “the answer”.

### Policy engine

- Deterministic. Versioned rules. Same inputs ⇒ same outputs.
- The only component that emits the official disposition enum.
- Must be unit-testable without mocks of models.

### Evaluation harness (coding + later runtime)

Structure in `evals/`:

| Piece | Role |
| --- | --- |
| `evals/scenarios/` | Deterministic fixtures with **explicit ground truth** |
| `evals/baselines/` | Frozen outputs to detect regressions once a runner exists |
| `evals/benchmarks/` | Cost, latency, and quality comparisons |
| traces | Decision traces: inputs, disposition, evidence IDs, budgets, cost, latency |
| `scripts/eval_smoke.py` | Validates scenarios and emits a smoke trace (no detection) |

Scenarios without `ground_truth` fail CI. Replay means: given the scenario payload, the future runtime (and today’s smoke) produces a comparable trace.

## Memory (boundary only)

No vector database. No RAG layer. Memory is **files and structured records**, not embeddings.

1. **Coding memory** — architecture decisions, conventions, implementation patterns, known pitfalls. Lives in git: especially `docs/DECISIONS.md`, this file, `AGENTS.md`, and architecture tests. Agents must read these rather than “recalling” unofficial lore.
2. **Incident memory** — previous incidents, evidence, hypotheses, root causes, affected cohorts, resolutions. Future product records with stable IDs. Not a prompt stuffing of past tickets. Not implemented in this harness.

Do not mix the two. A coding agent must not treat incident history as a source of SQL or as unbounded context. A runtime agent must not treat `docs/DECISIONS.md` as evidence for a payment incident.

## Skills (where they will live)

Project Skills, when introduced, live at `.cursor/skills/<name>/SKILL.md`. They are **not** installed now. See `docs/SKILLS.md`.
