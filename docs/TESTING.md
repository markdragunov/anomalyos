# Testing

## Principles

1. **Deterministic by default.** Unit tests use no network, no Docker, no wall-clock time, and
   no unseeded randomness. A failing unit test must fail the same way on every machine.
2. **Opt-in integration.** Tests needing live services are marked `integration` and are
   *skipped with a stated reason* unless `PULSEOS_RUN_INTEGRATION=1`. CI always runs them.
3. **Contracts over internals.** Tests assert layer contracts from `ARCHITECTURE.md` (inputs,
   outputs, invariants, failure modes), so refactors do not break them.
4. **No silent failures.** Failed or skipped tests are reported, never hidden or deleted to go green.

## Test levels

| Level | Location | Needs | Runs |
|---|---|---|---|
| Architecture (harness) | `tests/architecture/` | nothing (stdlib `unittest`, Python 3.12) | always; `harness.yml` |
| Unit | `tests/unit/` | nothing (`pytest`); SQL tests use a live ClickHouse when `PULSEOS_RUN_INTEGRATION=1`, else embedded `chdb`, else skip | always |
| Integration | `tests/integration/` | ClickHouse | opt-in locally, always in CI |
| Slow | marker `slow` | minutes of CPU (full-scale seed 42 golden digest) | opt-in: `PULSEOS_RUN_SLOW=1` |
| Scenario (future) | `tests/scenarios/` | ClickHouse | per scenario: ground truth vs system output |
| Evaluation | `evals/` (scenario JSON with `ground_truth`, `scripts/eval_smoke.py`) | recorded model responses | deterministic replay by default; live runs are explicit |

## Required coverage for every feature

Happy path · edge cases · invalid inputs · deterministic behaviour (same input → same output) ·
regression protection for anything a scenario once caught.

## AI components (future stages)

Jev returns typed answers, so tests target the question set and the code around it:

- **State builder tests (deterministic):** same candidate → identical `JevState`; buckets map
  exact values correctly at edges; no raw events, no scenario ids, no ground truth leak into state.
- **Answer validation tests:** unknown question ids, options outside the vocabulary,
  probabilities not summing to ~1, out-of-range values, missing answers → rejected, fallback used.
- **Composition tests:** composite scores and confidence routing are pure functions of
  recorded answers; thresholds are tested at their boundaries.
- **Failure tests:** 401 / 422 / 429 / 529, timeouts → deterministic fallback, pipeline completes.
- **Recorded-answer fixtures:** answers are recorded with the question-set version and returned
  model version, and replayed by default so CI stays deterministic and offline.
- **Live evaluation (explicit, opt-in):** scores the question set on scenarios — cause accuracy,
  incident/noise separation, and calibration (does confidence match observed accuracy).
- **Adversarial checks:** states containing injected instructions must not move answers
  beyond tolerance; external free text stays excluded unless screened.

## Commands

```bash
pytest                                  # unit (integration skipped with reason)
PULSEOS_RUN_INTEGRATION=1 pytest      # unit + integration (ClickHouse must be up)
pytest -m integration                   # integration only
```

## Current suite (Stage 0)

| File | What it protects |
|---|---|
| `unit/test_config.py` | defaults, overrides, purity, immutability, password redaction, invalid values |
| `unit/test_clickhouse.py` | health contract against an in-process fake: healthy, bad creds, bad ping, unreachable, no creds in URL |
| `unit/test_cli.py` | `doctor` exit codes for invalid config and unreachable server |
| `integration/test_clickhouse_live.py` | real ClickHouse reachable, authenticated, reports version |

## Stage 1 suite (`tests/unit/simulation/`, ≈50 s)

| File | What it protects |
|---|---|
| `test_sim_determinism.py` | same seed → byte-identical files; replay → identical truth; seed/spec changes run_id; stream before first injection identical with/without scenarios |
| `test_sim_validation.py` | full stream valid; truth valid for all 14 scenarios; validator catches PAN, forbidden keys, dangling refs, truth leakage, time travel, duplicate ids, amount mismatch |
| `test_sim_scenarios.py` | catalog covers all required kinds; intensity profiles; spec invariants; cause vocabulary == DATA_MODEL v1; DATA_MODEL truth fields; incidents visible against control cohorts in the events |
| `test_sim_clickhouse.py` | pure flattening with explicit `unknown`; identifier validation; config via `load_settings`; DDL + load + SQL checks on embedded ClickHouse (skipped if `chdb` absent) |
| `integration/test_sim_clickhouse_live.py` | generate → load into real ClickHouse → all SQL checks 0 → aggregation in SQL |

## Golden digests and counterfactual (sim-1.x)

`tests/unit/simulation/test_sim_golden.py` pins run id, event count, event SHA-256 and truth digest for seed 5 / scale 0.05 (fast, always) and seed 42 / scale 1.0 (`slow`). A failing golden test means generator output changed: bump `GENERATOR_VERSION`, update the values in a separate commit, and say in the message what changed. `test_sim_counterfactual.py` checks `expected_impact` against the real difference between a world without and a world with one scenario (tolerance 0, and 1 for `checkout_regression`).
