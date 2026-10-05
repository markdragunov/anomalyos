---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "scripts/**/*.py"
---

# Python conventions

- Product code supports Python ≥ 3.11; harness scripts and architecture tests run on Python 3.12 with the standard
  library only.
- Prefer the standard library. A third-party package needs an ADR and an update of the dependency allowlist test
  (runtime: `clickhouse-connect`; dev: `pytest`).
- Type hints on public functions; explicit, typed interfaces between layers (dataclasses, closed vocabularies).
- Keep functions focused, pure where possible, and testable without I/O. No speculative frameworks or abstractions.
- Module docstrings state why / input / output / invariants / failure modes; match the density of the surrounding code.
- Configuration only via `pulseos.config.load_settings`. Money in integer minor units + ISO 4217 currency; timestamps
  UTC; missing dimensions are the explicit value `unknown`.
- IDs and randomness in the simulator via `ids.stable_id` / `ids.derive_seed`; never `hash()` or global counters.
