# Decisions

Record important, durable choices here. Newest first. Coding memory lives in this file (and in `AGENTS.md` / architecture tests), not in a vector store.

Format: short ID, status, context, decision, consequences.

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
