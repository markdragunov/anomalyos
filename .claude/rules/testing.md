---
paths:
  - "tests/**/*.py"
  - "scripts/**/*.py"
  - "src/pulseos/**/*.py"
  - "evals/**"
---

# Testing

Purpose: every behavioural change is tested, deterministic and reported honestly.

- Behavioural changes come with tests in the same change. Seeded, deterministic fixtures only: no wall clock, no
  unseeded randomness, no network in unit tests (`INV-016`).
- Test levels (`docs/TESTING.md`): architecture (`tests/architecture/`, stdlib `unittest`, Python 3.12, no install);
  unit (`tests/unit/`, pytest; SQL tests use live ClickHouse with `PULSEOS_RUN_INTEGRATION=1`, else embedded chdb,
  else skip with a reason); integration (`tests/integration/`, opt-in); `slow` (`PULSEOS_RUN_SLOW=1`); evaluation
  (`evals/`, `scripts/eval_smoke.py`, DEV-seed scripts — never `HELDOUT_SEEDS` outside the benchmark stage).
- Never skip, delete or loosen a failing test to go green. Report failed and skipped tests with their reasons.
- Golden digests change only deliberately: bump `GENERATOR_VERSION`, update the values in a separate commit and say
  what changed.
- `scripts/check_architecture.py` and `scripts/eval_smoke.py` stay runnable without `pip install`.
- Every file in `evals/scenarios/` is valid JSON with explicit `ground_truth`; do not commit `evals/output/` or `reports/`.

Run: `python3 -m unittest discover -s tests/architecture -t . -v` · `python3 scripts/check_architecture.py` ·
`python3 scripts/eval_smoke.py` · `pytest` when `src/` or `tests/unit|integration` changed.
