# AnomalyOS agent contract

AnomalyOS is an AI Incident Intelligence platform for Billing & Payments.

This file is the **primary engineering contract** for Claude, Cursor, and any other coding agent working in this repository. Read it before changing code or architecture. If an instruction here conflicts with convenience, this file wins.

The repo currently contains the **coding harness only**. Do not implement product runtime (ingestion, ClickHouse schemas, detectors, Jev, policy, investigation, or UI) until that work is explicitly requested.

Canonical detail lives in:

- `docs/ARCHITECTURE.md` — boundaries, data flow, Mode A / Mode B, harness vs runtime
- `docs/INVARIANTS.md` — numbered invariants, violations, future tests
- `docs/DECISIONS.md` — recorded architecture and dependency decisions
- `docs/PRODUCT.md` — product intent (not an implementation plan to execute now)
- `docs/TOOLS.md` — which tools are required, later, or out of scope
- `docs/SKILLS.md` — future project Skills (do not install marketplace Skills)

## Non-negotiable rules

1. **ClickHouse will be the analytical source of truth.** Operational stores may exist later; analytical questions are answered from ClickHouse (through approved interfaces), not from raw logs or model memory.
2. **AI must never receive unbounded raw event streams.** Agents and LLMs see aggregates, cohorts, bounded evidence, and typed query results — never a firehose of payment events.
3. **AI must never generate arbitrary SQL.** No free-form query strings from a model to ClickHouse or any database.
4. **AI accesses analytical data through approved typed interfaces.** Named, reviewed, parameterized functions or views. New access paths require an architecture review and an invariant check.
5. **Jev is side-effect free.** Jev is a pure decision function. It does not write, notify, mutate state, or call I/O.
6. **Jev cannot execute tools.** Jev may inspect structured inputs and return a decision. It must not invoke tools, agents, or APIs.
7. **Policy is deterministic.** Same policy inputs + same rules ⇒ same `IGNORE` / `DIGEST` / `INCIDENT` disposition. No hidden LLM call inside policy.
8. **Investigation Agent is read-only in V2.** It may fetch evidence through approved interfaces. It must not retry charges, issue refunds, change config, page systems, or otherwise mutate billing state.
9. **LLM explanations may only reference validated evidence.** If a claim is not backed by an evidence object with a stable ID, it does not belong in the explanation.
10. **Important AI decisions must be auditable.** Persist who/what decided, on which inputs, under which policy version, with which evidence IDs.
11. **Evidence must have stable identifiers.** Evidence IDs do not change across replay. Traces reference IDs, not anonymous blobs.
12. **Observed, inferred, estimated, and recommended information must remain distinct.** Never present a guess as a measurement. Label epistemic status in data and in prose.
13. **Agent execution must have explicit budgets.** Tool calls, tokens, wall-clock, and fan-out are capped. Exceeding a budget is a hard stop, not a retry loop.
14. **No autonomous irreversible actions in V2.** Humans (or a later, explicitly designed control plane) approve anything irreversible. Coding agents must not add silent auto-remediation.
15. **Evaluation scenarios must contain explicit ground truth.** A scenario without `ground_truth` is invalid and must fail the harness.

An AI coding agent **must not silently violate an invariant**. If a change would weaken or bypass an invariant, stop, name the invariant, and propose a documented decision instead of quietly proceeding.

## Principle

**Jev decides. Code routes. LLM explains. Agent investigates.**

Do not invert this. LLMs do not disposition incidents. Agents do not decide policy. Jev does not call tools.

## Two harnesses (do not conflate)

| Harness | Purpose | What lives here now |
| --- | --- | --- |
| **Coding harness** | How agents change *this repository* safely | `AGENTS.md`, docs, architecture tests, eval structure, CI, Cursor rules |
| **AnomalyOS runtime harness** | How the *product* detects, decides, investigates, and explains | Documented only. Not implemented. |

Runtime (documented, not built):

Events → ClickHouse → Metric Aggregation → Anomaly Detection → Cohort Intelligence → Jev Decision Layer → Policy Engine → `IGNORE` / `DIGEST` / `INCIDENT` → Investigation Agent → Evidence → Jev → LLM Explanation

## Workflow

Every non-trivial change follows:

**READ → PLAN → IMPLEMENT → TEST → ARCHITECTURE CHECK → EVALUATE → REVIEW → COMMIT**

1. **READ** — relevant docs, invariants, existing tests, and surrounding code. Do not skip `docs/INVARIANTS.md` for behavioral changes.
2. **PLAN** — name the files, invariants, and tests you will touch. If product runtime is requested, say so explicitly; if it is not, do not sneak it in.
3. **IMPLEMENT** — smallest change that matches the plan. No speculative frameworks.
4. **TEST** — run `python -m unittest discover -s tests -t . -v`.
5. **ARCHITECTURE CHECK** — run `python scripts/check_architecture.py`. Failures are blockers, not warnings.
6. **EVALUATE** — run `python scripts/eval_smoke.py`. New scenarios need explicit ground truth.
7. **REVIEW** — read the diff. Check secrets, invariant bypasses, duplicated instructions, and dependency creep.
8. **COMMIT** — descriptive message; no secrets; no generated eval output.

## Coding conventions

- Prefer the Python standard library. Record any new dependency in `docs/DECISIONS.md` before adding it.
- Do not add product packages (`src/` detectors, ClickHouse clients, LLM wrappers) during harness-only work.
- Keep Cursor project rules focused. Do not copy this file into `.cursor/rules`.
- Do not install marketplace / external Skills. Future Skills are listed in `docs/SKILLS.md` and must be introduced only when the procedure they encode exists.

## Testing and evaluation

- Architecture tests enforce **repository constraints**, not missing product behavior.
- Eval scenarios are deterministic, replayable, and must include `ground_truth`.
- Cost and latency fields belong on traces even when the value is zero (harness smoke).
- Do not pretend to evaluate anomaly detection until detectors exist.

## Git discipline

- Never commit secrets, credentials, or `.env` files.
- Do not commit `evals/output/` or other generated traces.
- Do not rewrite published history on `main`.
- If an invariant or public contract changes, update `docs/INVARIANTS.md` and the architecture tests in the same change.

## Cursor Cloud

Use Python 3.12+. No extra install is required for the harness. Run architecture checks and eval smoke before finishing a change.
