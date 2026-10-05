# Architecture

This document describes system boundaries for PulseOS. The **coding harness is implemented**. The **runtime is built stage by stage** (see *Stage status* at the end): layers not marked done there are documented, not implemented.

## Two harnesses

### Coding harness (implemented)

Purpose: let coding agents (Claude Code is the primary one, ADR-036) and humans change PulseOS **safely and repeatedly**.

It enforces architecture, invariants, tests, evaluation structure, cost/latency trace shape, documentation, and git discipline.

Contents: `AGENTS.md`, `CLAUDE.md`, `.claude/` (rules, skills, settings), `docs/`, `tests/architecture/`, `evals/`, `scripts/`, `.github/workflows/`.

It is **not** the product. It does not ingest events, talk to ClickHouse, call an LLM, or open incidents; the runtime under `src/` does (to the extent its stage is done), and the harness constrains it.

### PulseOS runtime harness (built stage by stage)

Purpose: detect billing/payment anomalies, disposition them, investigate, and explain — with Jev as the only decision authority.

Documented flow (the numbered layer contracts and their status are below):

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

## Major components (runtime)

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

Project Skills, when introduced, live at `.claude/skills/<name>/SKILL.md`. None exist yet. See `docs/SKILLS.md` and `.claude/skills/README.md`.

---

# Runtime layer contracts

Imported from the former simulator tree. Layers are numbered 1–11; the narrative above (Mode A / Mode B, roles) is the same system seen from the decision flow. Incident lifecycle and storage are fixed by ADR-028 (spec 07 holds the transition table).

## Overview

```
          Billing events (synthetic generator, later real sources)
                              │
                              ▼
   ┌──────────────── DETERMINISTIC ZONE ─────────────────────────────┐
   │  1 Event layer ──► 2 ClickHouse (source of truth)               │
   │                           │                                     │
   │                           ▼                                     │
   │                    3 Metrics layer                              │
   │                           │                                     │
   │                           ▼                                     │
   │                   4 Detection engine ──► 5 Cohort engine        │
   │                                              │                  │
   │                                   Incident candidate            │
   │                           (compact, structured, versioned)      │
   └──────────────────────────────────┼──────────────────────────────┘
                                      ▼
   ┌──────────────── AI ZONE (advisory, validated) ──────────────────┐
   │                 6 Jev layer (TypeSafe System One)               │
   │     typed questions → probabilities + confidence, no text       │
   └──────────────────────────────────┼──────────────────────────────┘
                                      ▼
   ┌──────────────── DETERMINISTIC ZONE ─────────────────────────────┐
   │                     7 Incident engine                           │
   │          (lifecycle, thresholds, policy, audit log)             │
   └───────────────┬──────────────────────────────┬──────────────────┘
                   ▼                              ▼
     8 Investigation agent (AI, read-only)    9 API ──► 10 UI ──► Human decision
                   │  explicit tools only             ▲
                   └──────────────► evidence ─────────┘

   11 Evaluation layer: replays scenarios with ground truth and scores every layer.
```

## The deterministic / AI boundary

| Concern | Deterministic code | AI (Jev / agent) |
|---|---|---|
| Reading raw events | Yes | **Never** |
| Computing metrics, baselines, statistics | Yes | No |
| Deciding a change is statistically anomalous | Yes | No |
| Cohort decomposition and contribution ranking | Yes | No |
| Judging business meaningfulness, ranking hypotheses | Validates the output | Proposes |
| Thresholds, policy, permissions, workflow transitions | **Owns** | Cannot modify |
| Proposing investigation steps | Executes only allow-listed read tools | Proposes |
| Irreversible actions, incident resolution | Human only | **Never** |

Rules that hold across the boundary:

1. **Compact state in.** AI receives a bounded, structured, schema-versioned snapshot
   (metric series summaries, detector results, ranked cohorts, context), never raw rows.
2. **Structured output out.** Jev returns typed answers natively (probabilities + confidence,
   no text to parse). Code still validates every answer against the question it asked (known
   question ids, options, ranges) and degrades to a deterministic default on any failure.
3. **Advisory, not authoritative.** AI output is an input to deterministic rules. It cannot
   trigger an irreversible effect directly.
4. **Audited.** Every AI call records: the exact `state` and `questions` sent (or hash +
   storage ref), question-set version, the model version *returned by the API* (e.g.
   `jev-1.13.0`, not the requested alias), raw answers, validation result, timestamp, and the
   decision it influenced.
5. **Replaceable.** Each AI component has a deterministic fallback so the pipeline runs
   (with lower quality) when the model is unavailable. Evaluation compares both.

## Layer contracts

### 1. Event layer
- **Responsibility:** produce billing domain events (synthetic generator first) in a single
  versioned envelope; validate before storage.
- **Input:** scenario config + seed (synthetic) or source records (future).
- **Output:** validated events in the envelope defined in `DATA_MODEL.md`.
- **Invariants:** same scenario + seed → byte-identical event set; `event_id` unique and
  stable; money in integer minor units; timestamps UTC; invalid events rejected, not coerced.
- **Failure modes:** schema violation, duplicate IDs, out-of-order/late events (must be
  representable, not dropped silently).

### 2. ClickHouse
- **Responsibility:** analytical source of truth for events and derived aggregates.
- **Input:** validated events (batch insert).
- **Output:** query results for the metrics layer; read-only views for the agent tools.
- **Invariants:** idempotent ingestion (re-ingesting a batch does not double count);
  schema changes are versioned migrations; no business logic hidden in ad-hoc SQL outside
  the metrics layer.
- **Failure modes:** unavailable, partial insert, schema drift.

### 3. Metrics layer
- **Responsibility:** named, versioned metric definitions (e.g. authorization rate, renewal
  success rate, refund rate, revenue collected) computed over time windows and dimensions.
- **Input:** ClickHouse tables; metric definition; window; grain; dimension filters.
- **Output:** time series of `(window_start, dimensions, numerator, denominator, value)`.
- **Invariants:** rates always carry numerator and denominator (never a bare ratio); one
  canonical definition per metric name + version; deterministic for a fixed dataset.
- **Failure modes:** zero denominators, sparse cohorts, incomplete trailing window.

### 4. Detection engine
- **Responsibility:** decide whether a metric series has changed beyond expected variation.
  Answers **"what changed?"**.
- **Input:** metric series, baseline configuration, detector configuration.
- **Output:** `AnomalySignal` — metric, scope, window, observed vs expected, effect size,
  statistical strength, direction, detector name + version.
- **Invariants:** deterministic; thresholds live in configuration owned by code, not by AI;
  minimum-sample rules prevent alarms on noise; every signal is reproducible from its inputs.
- **Failure modes:** alarm on noise (false positive), missed slow drift, seasonality mistaken
  for incident, duplicate signals for one underlying change.

### 5. Cohort engine
- **Responsibility:** decompose an anomaly into the cohorts that explain it (PSP, payment
  method, country, card brand, platform, app version, plan, …) and group correlated signals.
- **Input:** `AnomalySignal`, dimension catalogue, ClickHouse access through the metrics layer.
- **Output:** `IncidentCandidate` — the signal(s), ranked cohorts with contribution to the
  change, affected volume, preliminary impact estimate, related signals.
- **Invariants:** deterministic ranking; contributions reconcile to the parent change within
  a stated tolerance; bounded size (top-k cohorts) so the candidate stays compact.
- **Failure modes:** combinatorial explosion, Simpson's-paradox style mix shifts, sparse cells.

### 6. Jev layer

Jev is TypeSafe's **System One** model (https://docs.typesafe.ai). It is not a generative
LLM: it evaluates a `state` against a set of typed, independent questions in one request and
returns probabilities. That shape fits this layer's job — fast, calibrated judgments that code
branches on — and it constrains the design (see ADR-018).

- **Responsibility:** answer **"is this likely meaningful? how strong is the evidence? which
  hypotheses are plausible?"** as a set of atomic, calibrated judgments that deterministic code
  combines.
- **Input:** a `JevState` built by code from the `IncidentCandidate`: small, named, semantic
  fields — pre-computed values and named buckets (e.g. `drop_severity: "severe"`,
  `onset: "started_2h_ago"`, `top_cohort: {psp: "psp_b", card_brand: "visa"}`), plus bounded
  context (recent deploys, maintenance windows). Never raw events, never free-text from
  external systems unless deliberately screened.
- **Questions:** a versioned **question set** owned by code, e.g.
  - `Noul` — "Is this change likely a real incident rather than normal variation?"
  - `Choice` over the closed **cause vocabulary** (`DATA_MODEL.md`) — which cause fits best
    (relative ranking)
  - one `Noul` per candidate cause — is this cause plausible at all (absolute; all may be low)
  - `Score` per evidence dimension — e.g. cohort concentration, temporal alignment with a
    deploy, consistency across related metrics
  - `Noul` per item in the evidence checklist — is this evidence present in the state (drives
    the "missing evidence" list)
- **Output:** `Assessment` assembled **in code** from the typed answers: incident probability,
  cause distribution + per-cause plausibility, evidence scores, missing-evidence list, and the
  confidences. Composite scores and thresholds are computed in code with weights code owns.
- **Invariants:** Jev never does arithmetic, counting, or date comparison (code does, then
  passes results or buckets); one judgment per question; the question set and cause
  vocabulary are versioned and changed only by code; Jev cannot change thresholds or incident
  state; no generated text — any human-readable explanation is rendered by code from the
  typed answers and the input state; every call is audited; a deterministic fallback
  assessment exists.
- **Structural caveat:** answers to different questions are not guaranteed to satisfy
  identities (a Noul and its negation need not sum to 1; a Choice and per-option Nouls answer
  different questions). Thresholds are tuned per question, never carried across question types.
- **Failure modes:** low confidence (routed, not acted on), state too large or noisy
  (accuracy drops — filter first), literal misreading of a badly worded question,
  adversarial text in state, API errors (401/422/429/529), model version change.

### 7. Incident engine
- **Responsibility:** own the incident lifecycle and policy.
- **Input:** incident candidates, Jev assessments, human decisions.
- **Output:** incidents with state, severity, impact, linked evidence, and an append-only audit log.
- **States (ADR-028):** `DETECTED → INVESTIGATING → ACKNOWLEDGED → ESCALATED → RECOVERING →
  RESOLVED | DISMISSED` (transition table in `docs/specs/07_INCIDENT_ENGINE.md`); stored append-only in ClickHouse.
- **Invariants:** transitions are explicit rules in code; `RESOLVED` and `DISMISSED` require
  a human actor; deduplication merges candidates of the same underlying incident; every
  transition is logged with actor (system / Jev-advised / human) and reason.
- **Failure modes:** duplicate incidents, flapping open/close, orphaned candidates.

### 8. Investigation agent
- **Responsibility:** answer **"what should I investigate next? which evidence is missing?
  which hypothesis should I test?"** and gather that evidence.
- **Shape:** a **deterministic loop in code**, not an autonomous model. Each step, code builds
  the state (incident, current assessment, evidence so far, remaining budget) and asks Jev
  a `Choice` over the allow-listed tools plus a `stop` option, and `Choice`s over the closed
  argument sets for that tool (cohort dimensions, metric names, windows). Code validates the
  selection, executes the tool read-only, attaches the result as evidence, and re-assesses.
  This follows TypeSafe's function-calling pattern: tool names and closed-set arguments as
  confidence-aware questions.
- **Input:** an incident, its Jev assessment, the tool registry, a step and cost budget.
- **Output:** investigation steps (tool, arguments, result, confidences) attached as evidence.
- **Invariants:** read-only; typed, allow-listed, parameter-bounded tools (never free-form SQL
  or browser automation); low-confidence tool selection stops and hands off to a human;
  budgets are enforced by counters; cannot change incident state beyond adding evidence;
  cannot close; each step seeks at least one disconfirming check for the leading hypothesis
  (enforced by the loop, not trusted to the model).
- **Failure modes:** looping, irrelevant queries, over-reading data, confirmation bias.
- **Open:** whether a generative model is ever needed here (e.g. a prose summary for the
  human). Default is templated summaries from typed data; adding an LLM requires an ADR.

### 9. API
- **Responsibility:** expose incidents, evidence, assessments, and human decisions to clients.
- **Invariants:** human decisions are authenticated and attributed; the API exposes no raw
  AI-controlled write paths to policy.

### 10. UI
- **Responsibility:** incident list, incident detail (what changed, cohorts, impact,
  hypotheses, evidence, audit trail), and the human decision action.
- **Invariants:** every number shown is traceable to its source query/metric version;
  AI-generated text is visibly labelled as such.

### 11. Evaluation layer
- **Responsibility:** run scenario suites with ground truth and score each layer
  independently (detection, localization, assessment, investigation, end-to-end).
- **Invariants:** fixed seeds; results versioned by code + config + model/prompt version;
  regressions are visible across runs; AI evaluations can use recorded responses for
  deterministic replay.

## Cross-cutting

- **Determinism:** seeds everywhere; injected clock; no hidden randomness.
- **Configuration:** typed and validated at startup (`pulseos.config`).
- **Security:** no secrets in git; dev services bind to localhost; AI never receives credentials.
- **Observability of the system itself:** structured logs and the audit log; no external
  telemetry stack in the prototype.

## Stage status

| Layer | Status |
|---|---|
| Config, ClickHouse dev service, health check | **Stage 0 — done** |
| 1 Events | **Stage 1** — deterministic Stripe-shaped generator, 14 scenarios with ground truth (`docs/SIMULATION.md`); normalized DATA_MODEL envelope (`events_norm`, Stage 2, ADR-022 A / ADR-025) |
| 2 ClickHouse schema | **Stage 1** — `events` (raw + flattened dimensions), `<db>_truth.*`; idempotent reload by `run_id` partition |
| 3 Metrics | **Stage 2** — ten versioned metrics computed in ClickHouse (`docs/METRICS.md`, ADR-026 for grain) |
| 4 Detection | **Stage 3** — first-look series, lagged baseline, stabilized z, CUSUM, prefilter; DEV recall 0.89 / 0.53 FP per day (`docs/DETECTION.md`) |
| 5 Cohorts | **Stage 4** — pooled before/during decomposition, BH-controlled cohorts, concentration rule, new-cohort test, bounded evidence bundle; DEV top-1 exact locus 44 % (61 % equivalence-aware) vs 1–2 % naive (`docs/COHORTS.md`) |
| 6 Jev | **Stage 5** — first-look JevState v2, question set v1, `JevClient` port with fake / replay / unconfigured HTTP transport, verifier; **Jev itself not evaluated (no access, OQ-1)** (`docs/DECISIONING.md`) |
| 6b Policy | **Stage 5** — deterministic `policy_v1`, no-Jev `baseline_v2`, failures → DIGEST, append-only `jev_decisions` audit |
| 7 Incident engine | Not started |
| 8 Agent | Not started (deliberately excluded from Stage 0) |
| 9–10 API, UI | Not started (deliberately excluded from Stage 0) |
| 11 Evaluation | Not started (ground truth it will score against exists since Stage 1) |
