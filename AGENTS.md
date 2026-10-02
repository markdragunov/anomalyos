# AnomalyOS agent contract

AnomalyOS is an AI Incident Intelligence platform for Billing & Payments.

This file is the **primary engineering contract** for Claude, Cursor, and any other coding agent working in this repository. Read it before changing code or architecture. If an instruction here conflicts with convenience, this file wins.

The repo holds the **coding harness** and the **product runtime, built stage by stage** (`docs/ARCHITECTURE.md` → *Stage status*; current task brief in `docs/tasks/`). Work only on the stage you were asked to do. Do not implement a later layer (metrics, detection, cohorts, Jev, policy, incident engine, investigation, API, UI) early; each needs its own request, and an ADR if it adds a dependency or a top-level package. Architecture tests enforce this.

Canonical detail lives in:

- `docs/ARCHITECTURE.md` — boundaries, data flow, Mode A / Mode B, harness vs runtime
- `docs/INVARIANTS.md` — numbered invariants, violations, future tests
- `docs/DECISIONS.md` — recorded architecture and dependency decisions
- `docs/PRODUCT.md` — product intent and prototype scope
- `docs/DATA_MODEL.md`, `docs/SIMULATION.md`, `docs/TESTING.md` — event envelope and cause vocabulary, the synthetic world, test strategy
- `docs/specs/` — build specs per stage (target design; ADRs in `docs/DECISIONS.md` win on conflict)
- `docs/TOOLS.md` — which tools are required, later, or out of scope
- `docs/SKILLS.md` — future project Skills (do not install marketplace Skills)

## Non-negotiable rules

1. **ClickHouse will be the analytical source of truth.** Operational stores may exist later; analytical questions are answered from ClickHouse (through approved interfaces), not from raw logs or model memory.
2. **AI must never receive unbounded raw event streams.** Agents and LLMs see aggregates, cohorts, bounded evidence, and typed query results — never a firehose of payment events.
3. **AI must never generate arbitrary SQL.** No free-form query strings from a model to ClickHouse or any database.
4. **AI accesses analytical data through approved typed interfaces.** Named, reviewed, parameterized functions or views. New access paths require an architecture review and an invariant check.
5. **Jev is side-effect free.** Jev is a pure decision function. It does not write, notify, mutate state, or call I/O. (The decision contract is pure; the hosted-model call lives in a transport adapter with replay — ADR-024.)
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
16. **Ground truth is isolated** (`INV-015`). Ground truth is produced only by simulator code, lives apart from events (`<db>_truth`, `ground_truth.json`), and must never be readable by detection, metrics, cohort, Jev or agent code paths.
17. **Everything is seeded and reproducible** (`INV-016`). No wall clock, unseeded randomness, `hash()` or global counters in generation or tests. Simulator randomness comes from `ids.derive_seed(seed, <structural key>)`, IDs from `ids.stable_id`. Every new scenario needs a `TruthSpec` with `true_cause` from the `DATA_MODEL.md` vocabulary.
18. **Data conventions.** Money is integer minor units plus ISO 4217 currency; timestamps are UTC; missing dimensions are the explicit value `unknown`.
19. **Jev answers typed questions only.** Choice / Score / Noul over a small state; no arithmetic, counting or date comparison in Jev (code computes, passes values or buckets); hypotheses come only from the closed cause vocabulary; low confidence routes to a human. Jev never writes explanations or SQL (see ADR-018; networked Jev vs `INV-004`: ADR-024).

An AI coding agent **must not silently violate an invariant**. If a change would weaken or bypass an invariant, stop, name the invariant, and propose a documented decision instead of quietly proceeding.

## Principle

**Jev decides. Code routes. LLM explains. Agent investigates.**

Do not invert this. LLMs do not disposition incidents. Agents do not decide policy. Jev does not call tools.

## Two harnesses (do not conflate)

| Harness | Purpose | What lives here now |
| --- | --- | --- |
| **Coding harness** | How agents change *this repository* safely | `AGENTS.md`, docs, architecture tests, eval structure, CI, Cursor rules |
| **AnomalyOS runtime harness** | How the *product* detects, decides, investigates, and explains | Built stage by stage under `src/anomalyos/`: config, ClickHouse health, synthetic world + loader today. |

Runtime (target flow; see *Stage status* for what exists):

Events → ClickHouse → Metric Aggregation → Anomaly Detection → Cohort Intelligence → Jev Decision Layer → Policy Engine → `IGNORE` / `DIGEST` / `INCIDENT` → Investigation Agent → Evidence → Jev → LLM Explanation

## Workflow

Every non-trivial change follows:

**READ → PLAN → IMPLEMENT → TEST → ARCHITECTURE CHECK → EVALUATE → REVIEW → COMMIT**

1. **READ** — relevant docs, invariants, existing tests, and surrounding code. Do not skip `docs/INVARIANTS.md` for behavioral changes.
2. **PLAN** — name the files, invariants, and tests you will touch. Name the stage you are working in; if the change belongs to a later stage, stop and say so.
3. **IMPLEMENT** — smallest change that matches the plan. No speculative frameworks.
4. **TEST** — harness: `python -m unittest discover -s tests/architecture -t . -v`; product: `pytest` (add `ANOMALYOS_RUN_INTEGRATION=1` with ClickHouse up). Report failed or skipped tests explicitly; never silence them.
5. **ARCHITECTURE CHECK** — run `python scripts/check_architecture.py`. Failures are blockers, not warnings.
6. **EVALUATE** — run `python scripts/eval_smoke.py`. New scenarios need explicit ground truth.
7. **REVIEW** — read the diff. Check secrets, invariant bypasses, duplicated instructions, and dependency creep.
8. **COMMIT** — descriptive message; no secrets; no generated eval output.

## Coding conventions

- Prefer the Python standard library. Record any new dependency in `docs/DECISIONS.md` before adding it; the allowlist is pinned in `tests/architecture/test_no_product_implementation.py` (currently `clickhouse-connect`, ADR-020).
- Product code lives in `src/anomalyos/`, one package per layer, created in the stage that implements it. Module docstrings state why / input / output / invariants / failure modes.
- Configuration only via `anomalyos.config.load_settings` (a pure function over an env mapping).
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

## Commands

```bash
# Harness (stdlib only, Python 3.12+, no install)
python3 -m unittest discover -s tests/architecture -t . -v
python3 scripts/check_architecture.py
python3 scripts/eval_smoke.py

# Product (Python 3.11+)
docker compose up -d clickhouse
pip install -e ".[dev]"
set -a; source .env; set +a
anomalyos doctor
pytest                               # unit
ANOMALYOS_RUN_INTEGRATION=1 pytest   # unit + integration
anomalyos-sim generate --seed 42 --out data/run_42 --validate
anomalyos-sim load --in data/run_42 --replace
```

Run the harness checks before finishing any change, and the product tests when `src/` or `tests/unit|integration` changed. Do not merge to `main`; the owner merges.
