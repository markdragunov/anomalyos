# CLAUDE.md

PulseOS — AI incident intelligence for billing and payments, built stage by stage. Current stage and what exists:
*Stage status* in `docs/ARCHITECTURE.md` and `docs/specs/STATUS.md`; the active task brief is in `docs/tasks/`.

The canonical, tool-independent engineering contract is `AGENTS.md`, imported below. This file only says how to work
with it in Claude Code. If anything here seems to conflict with `AGENTS.md`, `AGENTS.md` wins.

## Where things are

| Need | Look in |
|---|---|
| Invariants (never violate silently) | `docs/INVARIANTS.md` |
| Decisions and their history | `docs/DECISIONS.md` (ADRs; next free number: check the file) |
| Layers, Mode A / Mode B, stage table | `docs/ARCHITECTURE.md` |
| Data contract, metrics, detection, cohorts, decisioning, incidents | `docs/DATA_MODEL.md`, `docs/METRICS.md`, `docs/DETECTION.md`, `docs/COHORTS.md`, `docs/DECISIONING.md`, `docs/INCIDENTS.md` |
| Simulator and ground truth | `docs/SIMULATION.md` |
| Test levels and switches | `docs/TESTING.md` |
| Build specs (ADRs win on conflict) | `docs/specs/` |

Contextual rules in `.claude/rules/` load when you work with matching files (architecture, stage guard, testing,
Python, ClickHouse, AI safety). Reusable procedures will live in `.claude/skills/` (none yet; see its README).
Read only what the task needs; do not load every document up front.

## Protocol for every task

1. **Locate** the stage and the brief (`docs/tasks/`). If the work belongs to a later stage or crosses a gate (⛔),
   stop and say so.
2. **Read** the relevant docs, invariants and surrounding code before editing.
3. **Plan** files, invariants and tests you will touch; for non-trivial work state purpose, input, output,
   invariants and failure modes first.
4. **Implement** the smallest change that satisfies the plan, matching surrounding style.
5. **Test** — choose by what changed:
   - always: `python3 -m unittest discover -s tests/architecture -t . -v`, `python3 scripts/check_architecture.py`,
     `python3 scripts/eval_smoke.py`;
   - `src/` or `tests/unit|integration` changed: `pytest` (live ClickHouse: `PULSEOS_RUN_INTEGRATION=1`);
   - simulator output changed: the golden tests, plus `PULSEOS_RUN_SLOW=1 pytest -m slow`.
6. **Review** the diff for secrets, invariant bypasses, duplicated instructions and dependency creep.
7. **Commit** in small, meaningful commits whose message explains why. Never merge to `main`; the owner merges.

## Final report

End every task with: what changed · decisions taken (ADR ids) · tests run with passed / failed / skipped and the
reason for each skip · anything not done or not verified · open questions. Report failures plainly.

@AGENTS.md
