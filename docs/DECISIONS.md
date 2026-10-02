# Decisions

Record important, durable choices here. Newest first. Coding memory lives in this file (and in `AGENTS.md` / architecture tests), not in a vector store.

Format: short ID, status, context, decision, consequences.

---

## ADR-023 — Single repository: harness and product runtime live together

**Status:** accepted (2026-10-02)

**Context.** Work existed in three places: this harness repo (docs, architecture tests, eval skeleton, "no runtime"), a nested, remote-less `anomalyos_simulator` git repo (Stage 0 foundation + Stage 1 synthetic world, with its own `CLAUDE.md` and ADR-001…013), and loose build specs. The two contracts conflicted (ADR-001, ADR-002, ADR-008 here forbid `src/`, dependency manifests, pytest and any ClickHouse client; the simulator needs all of them).

**Decision.** One repository, `markdragunov/anomalyos`. The simulator tree is imported as a single commit (no history preserved). Layout: `src/anomalyos/` (runtime), `tests/unit`, `tests/integration` (pytest, product), `tests/architecture` (stdlib unittest, harness), `docs/specs/` (build specs), `docs/tasks/` (current task briefs). `AGENTS.md` is the only agent contract; `CLAUDE.md` is a thin pointer to it. Simulator ADR-001…013 were renumbered to ADR-010…022 in this file (offset +9); code and docs were updated.

This ADR supersedes:
- **ADR-001** — runtime is now allowed, **stage by stage**: each new layer (metrics, detection, cohorts, Jev, incident engine, agent, API, UI) needs its Stage brief and, where it adds a dependency or top-level package, an ADR. Architecture tests were rewritten from "no product code" to "no code beyond the current stage" (forbidden layer filenames, dependency allowlist, forbidden top-level app directories stay enforced).
- **ADR-002** (partly) — the harness stays stdlib-only and runs without pip; the product uses `clickhouse-connect` (ADR-020) and `pytest` as the only dev dependency (ADR-011). A test pins the dependency allowlist so nothing is added without editing it and this log.
- **ADR-008** — ClickHouse is now provisioned (Docker Compose, ADR-012) and has a bounded client module (`anomalyos.simulation.clickhouse_load`, ADR-020).
- **ADR-009** (partly) — harness scripts remain Python 3.12 stdlib; product code targets Python ≥ 3.11.

**Consequences.** CI has two workflows: `harness.yml` (3.12, stdlib: architecture tests, architecture check, eval smoke) and `ci.yml` (product: ClickHouse service, pytest). The harness unittest run is scoped to `tests/architecture`; `tests/unit` and `tests/integration` run under pytest.

---

## ADR-024 — Jev "purity" vs a networked Jev (TypeSafe System One) · *proposed*

**Status:** proposed — needs an owner decision before any Jev integration (Stage 5).

**Context.** `INV-004` (Jev is side-effect free: no I/O, no HTTP, pure function of structured input) was written for an abstract decision function. ADR-018 (formerly simulator ADR-009) defines Jev as TypeSafe's hosted System One model, which can only be reached over the network. As written, a client calling that API violates `INV-004`.

**Options.** (a) Keep `INV-004` for the *decision contract* (typed question set + state in, typed answers out; no tools, no writes, no hidden reads), and place the network call in a separate transport adapter behind it, with recorded-answer replay as the default in tests and evaluation. (b) Amend `INV-004` explicitly to allow exactly one outbound call (the model request) and nothing else. (c) Implement Jev locally as deterministic code and treat the hosted model as a baseline.

**Recommendation.** (a) with replay. It preserves `INV-004`/`INV-005` for everything that matters (no side effects on billing state, no tools) and makes decisions reproducible. Until decided, no code may call a Jev endpoint, and `INV-004` is unchanged.

Related open points: ADR-018 says Jev does not generate text, so the "LLM explains" step needs a separate generative model and its own ADR; ADR-019 (formerly ADR-010, `proposed`) has Jev choose the tool from a closed set, while `docs/specs/08_INVESTIGATION_AGENT.md` has the agent choose the tool call.

---

## ADR-001 — Harness-only bootstrap; no product runtime

**Status:** accepted (2026-09-28)

**Context.** `main` was an empty initialize commit. Agents need a contract before detectors, Jev, or ClickHouse exist.

**Decision.** Ship documentation, architecture tests, eval *structure*, CI that can actually run, and Cursor rules. Do not add `src/` product packages, drivers, LLM clients, or UI.

**Consequences.** Architecture tests assert the absence of product implementation. Stage 0 Product Foundation is a later, explicit change. Do not weaken those tests to sneak runtime in.

---

## ADR-002 — Standard library only for the harness

**Status:** accepted (2026-09-28)

**Context.** Dependency policy: necessary? stdlib? coupling? reliability? deterministic tests?

**Decision.** Architecture tests and eval smoke use Python 3.12 **stdlib only** (`unittest`, `json`, `pathlib`, `re`, `time`). No `requirements.txt`, no pytest, no pydantic, no YAML library.

**Consequences.** Scenario files are JSON (not YAML) so parsing stays stdlib. GitHub Actions YAML is checked as text, not parsed with PyYAML. If a later stage needs a dependency, add it only after a new ADR.

---

## ADR-003 — `AGENTS.md` is the contract; Cursor rules stay thin

**Status:** accepted (2026-09-28)

**Context.** Cursor (2026) reads root `AGENTS.md` for all agents, and `.cursor/rules/*.mdc` for scoped project rules. Duplicating the contract into always-on rules wastes context and drifts.

**Decision.** Put the 15 rules, workflow, and principle in `AGENTS.md`. `.cursor/rules/` only contains focused reminders (safety pointer, docs, tests, no-product). No `.cursorrules`. No duplicate `CLAUDE.md` essay. No marketplace Skills.

**Consequences.** If Cursor behavior drifts, tighten a **scoped** `.mdc` rather than pasting `AGENTS.md` again.

---

## ADR-004 — Skills are planned, not stubbed

**Status:** accepted (2026-09-28)

**Context.** Empty `SKILL.md` files still get listed and pollute context. Procedures (ClickHouse, Jev, evals) do not exist yet.

**Decision.** Document future Skills in `docs/SKILLS.md`. Create `.cursor/skills/<name>/SKILL.md` only when the procedure is real.

**Consequences.** Coding agents follow `AGENTS.md` + docs until then.

---

## ADR-005 — Memory is files, not RAG

**Status:** accepted (2026-09-28)

**Context.** Need coding memory and future incident memory without a vector database.

**Decision.** Coding memory = git docs (this file, architecture, invariants). Incident memory = future structured records with stable evidence IDs. No embeddings, no RAG MCP.

**Consequences.** Agents must read docs rather than relying on unofficial chat memory. Incident search, when built, will be typed/filtered records, not similarity search over tickets.

---

## ADR-006 — CI runs only checks that exist

**Status:** accepted (2026-09-28)

**Context.** Eventual pipeline: PR → tests → lint → type checking → architecture checks → evaluation smoke.

**Decision.** Implement unittest + architecture check + eval smoke. Do **not** add fake `lint` / `typecheck` jobs (`exit 0`, echo skip). Introduce ruff/mypy (or equivalent) in a later ADR when there is product code to lint.

**Consequences.** The workflow file documents the eventual stages as comments only. `tests/architecture` asserts the workflow invokes the real commands.

---

## ADR-007 — Evaluation harness is structural

**Status:** accepted (2026-09-28)

**Context.** Need deterministic scenarios, ground truth, replay, baselines, benchmarks, traces, cost and latency — without anomaly detection.

**Decision.** JSON scenarios with required `ground_truth`. Smoke runner validates schema, writes a trace with `cost` and `latency_ms`, and does not score detectors. Baselines and benchmarks directories explain future capture; they do not contain invented detector scores.

**Consequences.** First real detector evals will add baselines in a dedicated change, not during harness bootstrap.

---

## ADR-008 — ClickHouse is documented SoT, not installed

**Status:** accepted (2026-09-28)

**Context.** Analytical source of truth will be ClickHouse. Installing it now would add ops surface with no product.

**Decision.** Do not add Docker Compose, clickhouse-connect, or schemas. Document approved-interface rules so they exist before the first driver import.

**Consequences.** Architecture tests forbid ClickHouse drivers until an ADR explicitly allows a bounded client module.

---

## ADR-009 — Python as the harness language

**Status:** accepted (2026-09-28)

**Context.** Need a test runner with zero dependencies. Runtime language is not frozen yet.

**Decision.** Use Python 3.12 for harness tests and scripts. Choosing Go/TS/Rust for the product later does not require rewriting these constraints; ports must preserve invariant IDs and eval JSON shape.

**Consequences.** Product code may live in another language. Architecture tests should then scan that language too (follow-up ADR).

---

# Decisions imported from the simulator tree

Originally ADR-001…013 of the former `anomalyos_simulator` repository, renumbered +9. Status and wording are preserved except where noted. Open questions (OQ-n) are listed at the end.

## ADR-010 — Standalone repository, separate from `ai-pm-case` · *accepted* (2026-10-02)

**Context.** The target repo `markdragunov/ai-pm-case` already contains a working project
(AI Recovery Engine, v0/v1/v2, passing tests) whose own CLAUDE.md forbids `pyproject.toml`,
dependencies and scope expansion. AnomalyOS needs all three.
**Decision.** Build AnomalyOS as its own repository. Do not modify `ai-pm-case`.
**Consequences.** Both projects keep coherent contracts. Confirmed by the owner: the single repository is `github.com/markdragunov/anomalyos`, which also
absorbs the former separate simulator tree (ADR-023).

## ADR-011 — Python 3.11+, `src/` layout, setuptools, pytest · *accepted*

**Context.** Need a standard, dependency-light, installable package.
**Decision.** `pyproject.toml` with setuptools backend; `src/anomalyos`; pytest as the only
dev dependency.
**Consequences.** `pip install -e ".[dev]"` is the whole setup. No lint tooling yet (add
with an ADR when a real need appears).

## ADR-012 — ClickHouse as analytical source of truth; local single node · *accepted*

**Context.** Billing analytics is append-heavy, aggregation-heavy, and dimension-sliced.
**Decision.** ClickHouse `25.8` (LTS) via Docker Compose, ports bound to localhost, named
volume. Same image pinned in CI.
**Consequences.** No production infra, clustering, or replication. Upgrades are explicit.

## ADR-013 — No ClickHouse client library in Stage 0 · *superseded by ADR-020*

**Context.** Stage 0 only needs a health check; choosing a client (e.g. `clickhouse-connect`)
before real query patterns exist is premature.
**Decision.** Use the HTTP interface via stdlib `urllib` for `/ping` and `SELECT version()`.
**Consequences.** Zero runtime dependencies. Stage 1 must decide the client with an ADR
(bulk insert performance and typed results are the criteria).

## ADR-014 — Configuration from process environment only · *accepted*

**Context.** Hidden `.env` loading makes behaviour depend on the working directory.
**Decision.** `load_settings(env)` is a pure function over a mapping; users export `.env`
explicitly; Docker Compose reads `.env` natively.
**Consequences.** Deterministic, easily testable config; one extra shell step for developers.

## ADR-015 — AI receives compact structured state; code owns policy · *accepted*

**Context.** Master context principles 2–9.
**Decision.** Jev and the agent never read raw events; inputs are versioned snapshots,
outputs are schema-validated; thresholds and lifecycle transitions live in code.
**Consequences.** Every AI component needs an input schema, output schema, validator,
fallback, and audit record before it ships.

## ADR-016 — Read-only agent; human-only resolution · *accepted*

**Decision.** The investigation agent uses allow-listed, typed, read-only tools and cannot
change incident state beyond attaching evidence. Only an authenticated human resolves or dismisses.

## ADR-017 — Ground truth stored separately from events · *accepted*

**Decision.** Scenario ground truth is derived from the scenario definition and stored where
no detection/AI component can read it; `scenario_id` is excluded from model inputs.
**Consequences.** Evaluation is non-circular.

## ADR-018 — Jev (TypeSafe System One) as the intelligence layer · *accepted*

**Context.** Jev is TypeSafe's System One model: one request evaluates a `state` against many
independent typed questions (`Choice`, `Score`, `Noul`) and returns probabilities, plus
confidence for Choice and Score. It does not generate text. Known weak spots (jev-1.13):
arithmetic, counting, date comparison, multi-hop indirection, large noisy state, literal
reading, adversarial content, no guaranteed cross-question identities.
**Decision.**
- Jev answers atomic judgments; code composes them (composite scoring) and owns weights and
  thresholds.
- Hypotheses come from a closed, versioned cause vocabulary; Jev ranks and rates, never invents.
- All numbers, counts and times are computed in code and passed as values or named buckets.
- States are small and filtered per question set; external free text is excluded by default.
- Confidence gates behaviour (act / flag / route to human), with thresholds per question and
  per risk level, calibrated on synthetic scenarios.
- No Jev-generated explanations; human-readable text is rendered by code from typed answers.
- Requests pin a model version for evaluation; the returned model version is always audited.
**Consequences.** The architecture's principles (compact state, code owns policy, auditable
AI) become natural rather than enforced after the fact. Assessment quality depends on the
quality of the question set, which becomes a first-class, versioned, tested artifact.
Narrative explanation, if ever needed, requires a separate generative model and its own ADR.

## ADR-019 — Investigation agent as a code-driven loop over Jev choices · *proposed*

**Context.** Jev selects; it does not plan or generate. The agent must be read-only and use
explicit tools.
**Decision.** The agent is a deterministic loop: code builds state, Jev chooses the next tool
and its closed-set arguments (or `stop`), code validates and executes, evidence is attached.
**Consequences.** Every step is auditable and bounded; no free-form tool arguments exist.
Revisit if investigation quality plateaus on scenarios that need open-ended exploration.

## ADR-020 — `clickhouse-connect` as the first runtime dependency · *accepted*

**Context.** Stage 1 loads ≈1.37M events per run. Hand-rolled `urllib` HTTP (ADR-013) would
need its own batching, compression, typed parameters and error handling. Resolves OQ-3.
**Decision.** Add `clickhouse-connect` (official client, Apache-2.0, HTTP transport, no native
build), pinned `>=0.8,<2`. Imported lazily and only by `anomalyos.simulation.clickhouse_load`;
generation and validation stay stdlib-only. The Stage 0 `doctor` health check keeps its
stdlib implementation. Inserts use `raw_insert(fmt="JSONEachRow")` in 50k-row batches.
**Alternatives.** stdlib `urllib` (no dep, more code); `clickhouse-driver` (native port 9000,
C extension); `numpy` for generation speed (not needed: ~1.5 min at scale 1.0).
**Consequences.** Supersedes the "no client library" clause of ADR-013. Transitive deps:
`urllib3`, `certifi`, `zstandard`, `lz4`, `pytz`. `chdb` (embedded ClickHouse) is used by one
unit test *if installed* to run DDL, loader and SQL checks without Docker; it is deliberately
not a declared dependency, and the test is skipped with a stated reason when absent.

## ADR-021 — Simulator: per-payment mechanisms, stream isolation, counterfactual truth · *accepted*

**Decision.** Scenarios inject mechanisms on individual payments (approval multiplier,
abandonment, refunds, duplicates, demand multiplier, exogenous stream), never metric
overrides. Every market-hour, effect-hour and renewal has its own RNG seeded from
`(seed, structural key)`; every PaymentIntent consumes a fixed-length uniform vector; IDs are
hashes of structural keys. Impact in ground truth comes from evaluating the same uniforms
with and without each effect (leave-one-out when effects overlap).
**Why.** Anomalies look organic in the events; a scenario perturbs nothing outside its
cohort/window (tested byte-for-byte); impact is exact and reproducible without any model.
Ground truth is derived from the scenario definition plus this counterfactual, never from the
emitted events (ADR-017) and lives in a separate database `<db>_truth`.
**Trade-offs.** Eager per-payment lifecycles cannot model feedback loops (customers churning
after failures changing future demand). Fixed UTC offsets, 30-day billing months.

## ADR-022 — Raw event format: Stripe-shaped source, DATA_MODEL envelope as normalized layer · *proposed*

**Context.** Stage 1's spec asked for Stripe-shaped objects and an `evt_…` envelope
(`api_version`, `data.object` snapshot, `request.idempotency_key`). `DATA_MODEL.md` defines a
different logical envelope (`event_id`, `event_type` from its own vocabulary, `occurred_at`/
`ingested_at`, explicit dimensions, `scenario_id`). Metrics, detection and evaluation must be
written against exactly one of them.
**Proposal.** Keep both, as two layers with one direction of dependency:
1. *Raw layer* — Stripe-shaped events, as generated (realistic source format; immutable
   snapshots; idempotency signal needed for duplicate detection). Stored verbatim in
   `events.data`.
2. *Normalized layer* — a pure, versioned mapping raw → DATA_MODEL envelope
   (`charge.failed` → `payment.declined` or `payment.failed` by failure class, `invoice.*` →
   `invoice.*`, renewal PI → `subscription.renewal_attempted`, …) into a table the metrics layer
   reads. Metrics, detection, cohorts and evaluation depend **only** on this layer.
Today the loader already produces a flattened table with DATA_MODEL dimension names and
explicit `unknown`; the event-type mapping, `occurred_at`/`ingested_at` and `merchant_id` are
not implemented yet.
**Alternatives.** (a) Generate the DATA_MODEL envelope directly and drop Stripe shapes —
simpler, but loses realistic source semantics and forces a rewrite of Stage 1. (b) Adopt the
Stripe shape as the logical contract — rewrites DATA_MODEL and ties every layer to one
vendor's model.
**Status.** Needs an owner decision before Stage 2 (metrics).

## Open questions

- **OQ-1 — Jev integration details** (resolved in principle by ADR-018). Still open: official
  Python SDK vs stdlib HTTP client (dependency trade-off), pinned model version for
  evaluation, context-window budget per state, API key handling (`.env` only, never in git),
  and whether evaluation replays recorded answers by default (recommended).
- **OQ-2 — Time grain.** Default metric grain for detection (5 min / 15 min / 1 h) — decide
  with the first detector, based on synthetic volume.
- **OQ-3 — ClickHouse client.** Resolved by ADR-020.
- **OQ-4 — Country semantics.** Issuer country vs customer country vs merchant country for
  cohort decomposition; likely all three as separate dimensions. Stage 1 emits
  `customer_country` and `issuer_country` as separate columns (single merchant, so no
  merchant country yet); scenario cohorts use `customer_country`.
- **OQ-5 — Question-set design.** The initial Jev question set and bucket edges; to be
  designed against scenarios 1, 10, 11 and 13 first (clear incident, slow drift, noise, recovery).
