---
paths:
  - "src/pulseos/metrics/**/*.py"
  - "src/pulseos/events/store.py"
  - "src/pulseos/detection/run.py"
  - "src/pulseos/simulation/clickhouse_load.py"
  - "src/pulseos/clickhouse.py"
  - "src/pulseos/cohorts/**/*.py"
---

# ClickHouse access

Purpose: ClickHouse is the analytical source of truth (`INV-003`); access to it stays typed, bounded and reproducible.

- Aggregate in ClickHouse; never pull raw event rows into Python for analysis. Analytical reads go through the
  existing interfaces (`metrics.compute`, the registry in `metrics/registry.py`, `events/store.py` for loading) or an
  explicitly approved new one.
- No arbitrary SQL from an LLM or agent (`INV-002`). SQL text is static and owned by code; every caller-supplied value
  is bound as a query parameter; identifiers are validated against allowlists; results are bounded (window and group
  limits).
- Keep `occurred_at` (business time) and `ingested_at` (delivery time) distinct. Detection reads first-look series
  (`visibility="window_close"`); never use information from after a window closed for a decision about it.
- Loads are idempotent per `run_id` (drop partition + insert) and self-checking (SQL checks must return 0).
- Window handling: grain-aligned starts, `end` exclusive, incomplete trailing windows flagged, `NULL` for a zero
  denominator.
- Ground truth (`<db>_truth`) is read only by the simulator and `evaluation/` (`INV-015`).
- A schema or metric change ships with tests (reference tests against a Python implementation) and a version bump
  (`(name, version)` in the registry, `NORMALIZATION_VERSION`); a new analytical interface needs an architectural
  justification (ADR).

Validate: `pytest tests/unit/metrics tests/unit/events` (live ClickHouse in CI via `PULSEOS_RUN_INTEGRATION=1`).
References: `docs/METRICS.md`, `docs/DATA_MODEL.md`, ADR-022, ADR-025, ADR-026, ADR-034.
