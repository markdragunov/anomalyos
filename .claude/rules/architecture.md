---
paths:
  - "src/anomalyos/**/*.py"
  - "tests/architecture/**/*.py"
  - "docs/ARCHITECTURE.md"
  - "docs/INVARIANTS.md"
  - "docs/DECISIONS.md"
---

# Architecture boundaries

Purpose: keep package boundaries, invariants and the two harnesses intact while changing runtime code or the
architecture tests.

- One package per architecture layer under `src/anomalyos/`, created only in the stage that implements it. A new
  package, top-level directory or dependency needs an ADR in `docs/DECISIONS.md` first.
- Do not conflate the **coding harness** (contract, docs, `tests/architecture/`, `evals/`, `scripts/`, `.claude/`, CI)
  with the **runtime harness** (`src/anomalyos/`). No detector in `scripts/`, no model call in an architecture test.
- Invariant IDs are stable (`INV-001` … `INV-016`). If an invariant's behaviour changes, update `docs/INVARIANTS.md`
  and `tests/architecture/` in the same change, with an ADR. Never weaken one silently: name it and stop.
- Durable decisions go into `docs/DECISIONS.md` (next free ADR number: check the file), not only into chat.
- Link `AGENTS.md` instead of copying it into other files.

Validate: `python3 scripts/check_architecture.py` (failures are blockers).
References: `docs/ARCHITECTURE.md`, `docs/INVARIANTS.md`, `docs/DECISIONS.md`.
