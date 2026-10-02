# Invariants

These are binding. A coding agent must not silently violate them. If a change requires relaxing one, record a decision in `docs/DECISIONS.md` and update this file **and** `tests/architecture/` in the same change.

IDs are stable. Do not renumber.

## INV-001 — No Raw Event → AI

**What it means.** Models, Jev, and agents never consume unbounded raw billing/payment event streams. Inputs are aggregates, cohorts, bounded query results from approved interfaces, or evidence objects.

**Why it exists.** Raw events are high-volume, PII-bearing, and easy to overflow context. They also train the system to “look at the logs” instead of using ClickHouse as analytical truth.

**How it could be violated.** Passing webhook payloads into an LLM prompt; dumping a ClickHouse `SELECT * FROM events` into agent context; streaming Kafka to a model.

**How it could be tested.** Static scan for LLM/agent call sites whose arguments include event-row types or unbounded iterables; runtime assertion that prompt builders accept only approved view DTOs; eval fixtures that fail if a scenario input is a raw event list.

## INV-002 — No AI → Arbitrary SQL

**What it means.** No model-generated SQL string is executed. No “ask the LLM to write a query” helper.

**Why it exists.** Arbitrary SQL bypasses review, leaks data, and makes evals non-deterministic. ClickHouse is powerful; that is exactly why access must be typed.

**How it could be violated.** `clickhouse.execute(llm.complete(...))`; agent tool `run_sql(query: str)`; interpolating natural language into `WHERE` clauses.

**How it could be tested.** Forbid unconstrained SQL tool signatures in architecture tests; allowlist of query modules; CI grep for `execute(` on non-literal SQL in AI-adjacent packages (once those packages exist).

## INV-003 — Approved Analytical Interfaces

**What it means.** Analytical reads go through named, reviewed, parameterized interfaces (functions, views, or query objects). New interfaces are explicit changes, not ad-hoc strings.

**Why it exists.** Makes access auditable, mockable, and evaluable. Preserves ClickHouse as SoT without giving agents a shell on the warehouse.

**How it could be violated.** A new notebook or agent tool that opens a raw driver session; copying SQL into `scripts/` “just this once”.

**How it could be tested.** Import graph: AI/agent packages may import `analytics.interfaces` (future) but not the ClickHouse driver; architecture test allowlist of modules permitted to construct SQL.

## INV-004 — Jev Is Side-Effect Free

**What it means.** Jev is a pure function of its structured inputs. No writes, no HTTP, no tool calls, no hidden reads.

**Clarification (ADR-024, accepted).** The *decision contract* is pure: a typed state and question-set version in, typed answers out, with no writes, tools or hidden reads. The hosted model call (TypeSafe System One) is made only by a separate transport adapter behind a `JevClient` port; tests, replay and evaluation use recorded raw responses, so the same recorded answers always reproduce the same decision. Nothing else may perform I/O on Jev's behalf.

**Why it exists.** Replay, audit, and tests require that the same inputs yield the same decision record. Side effects inside Jev make policy undebuggable.

**How it could be violated.** Jev logging to Slack; Jev reading ClickHouse; Jev incrementing a counter in Redis; catching the wall clock instead of using a `now` input.

**How it could be tested.** Jev module import graph forbids I/O libraries; property test: twice(Jev(x)) == Jev(x); fake filesystem/network in unit tests would still fail if Jev touches them.

## INV-005 — Jev Cannot Execute Tools

**What it means.** Jev must not be given a tool runtime, MCP client, or agent handle. It returns data, it does not act.

**Why it exists.** Tools are side effects. Mixing “decide” and “do” is how autonomous irreversible actions sneak in.

**How it could be violated.** Passing a `Toolbelt` into Jev; Jev emitting a `CallTool` instruction that a wrapper eagerly executes without policy.

**How it could be tested.** Type signature of Jev forbids tool interfaces; architecture test that `jev/` does not import `tools/` or agent runtimes.

## INV-006 — Policy Is Deterministic

**What it means.** Official disposition `IGNORE | DIGEST | INCIDENT` is computed by versioned rules from Jev’s record. Same inputs + same policy version ⇒ same output. No LLM inside policy.

**Why it exists.** Operators need to explain why something became an incident. Eval ground truth depends on it.

**How it could be violated.** Policy calling an LLM “to be flexible”; reading wall-clock randomness; unordered set iteration that changes output; silent rule defaults that differ per environment.

**How it could be tested.** Golden fixtures in `evals/scenarios/`; hash of (inputs, policy_version) → disposition; forbid network imports in policy package.

## INV-007 — Agent Is Read-Only in V2

**What it means.** The Investigation Agent may fetch evidence through approved read interfaces. It must not mutate billing, payments, config, or external customer state.

**Why it exists.** V2 is intelligence, not closed-loop remediation. A wrong refund or retry is worse than a delayed digest.

**How it could be violated.** Tools named `refund`, `retry_capture`, `disable_processor`, `page_oncall` on the investigation agent; write-capable ClickHouse credentials.

**How it could be tested.** Tool registry allowlist is GET/read only; architecture test fails if mutation tool names are registered to the V2 agent; integration tests against a fake billing API that errors on writes.

## INV-008 — LLM Uses Validated Evidence

**What it means.** Explanations may cite only evidence objects that passed validation and have stable IDs. No unsourced numbers. No inventing IDs.

**Why it exists.** Billing incidents will be read by operators and, later, customers. Hallucinated rates are operationally dangerous.

**How it could be violated.** Prompt includes free-floating statistics; model emits `evidence_id` values not in the bundle; post-processor that does not strip uncited claims.

**How it could be tested.** Checker: every numeric/claim span maps to an evidence ID in the bundle; evals with ground-truth citation sets; architecture test that explanation APIs require an evidence bundle.

## INV-009 — Evidence Is Traceable

**What it means.** Every evidence object has a stable identifier that survives replay. Traces and explanations refer to IDs.

**Why it exists.** Audit and evals cannot compare prose. They compare IDs and payloads.

**How it could be violated.** Evidence keyed by list index; regenerating UUIDs on every run; dropping IDs when serializing to the LLM.

**How it could be tested.** Replay two runs of the same scenario, assert evidence ID sets are equal; schema requires `id` on evidence.

## INV-010 — Decisions Are Auditable

**What it means.** Important AI-adjacent decisions persist: disposition, Jev record, policy version, evidence IDs, actor, budgets consumed, timestamps (as inputs).

**Why it exists.** Without an audit trail, “the model did it” is the only explanation — which this architecture forbids.

**How it could be violated.** Logging only the LLM essay; skipping audit on `IGNORE`; mutating a decision in place without a new record.

**How it could be tested.** Eval traces must include the audit shape; once runtime exists, integration test that each disposition writes one append-only record.

## INV-011 — Agent Budgets Are Bounded

**What it means.** Agent runs declare max tool calls, max tokens, max wall-clock, and max fan-out. Hitting a cap stops the run.

**Why it exists.** Cost, latency, and safety. Unbounded investigation is both expensive and a path to raw-data fishing (INV-001).

**How it could be violated.** Retry loops that reset counters; missing defaults that mean “infinite”; spawning sub-agents without a budget split.

**How it could be tested.** Scenarios require a `budgets` object (already enforced for harness fixtures that involve agents); unit tests that the runtime raises on cap; architecture test that agent entrypoints take a budget type.

## INV-012 — No Autonomous Irreversible Actions

**What it means.** V2 must not autonomously refund, capture, block a rail, page a partner, or otherwise take irreversible production action. Coding agents must not add a hidden auto-remediation path.

**Why it exists.** Incident intelligence first. Closed-loop control is a later, explicit product decision.

**How it could be violated.** A “helpful” webhook that retries failed charges; policy `INCIDENT` handler that disables a processor; an agent tool marked read-only that still POSTs.

**How it could be tested.** Allowlist of side-effect sinks is empty in V2; architecture tests fail on known mutation symbols in agent/policy packages; evals never include a ground-truth *action* that mutates money movement.

## INV-013 — Epistemic Status Is Distinct

**What it means.** `observed`, `inferred`, `estimated`, and `recommended` are different fields (or an explicit status enum). Copy and APIs must not collapse them.

**Why it exists.** Operators will treat “estimated 12% lift” as measured if the UI looks the same as an observed rate.

**How it could be violated.** A single `value` field with no status; LLM wording that states an estimate as fact; mixing recommended actions into metric tables.

**How it could be tested.** Schema enum on evidence and metrics; evals that fail if an estimated figure is labeled observed; copy lint later.

## INV-014 — Evaluation Ground Truth Is Explicit

**What it means.** Every eval scenario file includes a `ground_truth` object. Implicit “we’ll know it when we see it” is invalid.

**Why it exists.** Without ground truth, evals become vibes, and agents will green-wash regressions.

**How it could be violated.** A scenario with only inputs; ground truth in a Slack thread; optional JSON field skipped “until we have detectors”.

**How it could be tested.** `tests/architecture/test_eval_ground_truth.py` and `scripts/eval_smoke.py` — already enforced in this harness.

## Operating rules that are not numbered invariants

`AGENTS.md` rules 16–19 are binding repository rules but have no `INV-` id (adding invariants is an owner decision; see the open question in the closeout report). Their sources and enforcement:

| AGENTS.md rule | Source | Enforced by |
| --- | --- | --- |
| 16 — Ground truth is isolated | ADR-017 | Stage 2: test that greps `src/anomalyos/events` and `metrics` for `_truth` / `ground_truth` (to be added with that stage) |
| 17 — Seeded and reproducible | ADR-021 | simulator determinism tests (`tests/unit/simulation/test_sim_determinism.py`) |
| 18 — Data conventions (money, UTC, `unknown`) | `docs/DATA_MODEL.md` | simulator validator (`validate.py`) |
| 19 — Jev answers typed questions only | ADR-018, ADR-024 | Stage 5 tests (question-set and state-builder tests, `docs/TESTING.md`) |

Rules 1–15 map to `INV-001`…`INV-014` (rule 15 = `INV-014`).
