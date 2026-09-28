# AnomalyOS

AI Incident Intelligence for Billing & Payments.

This repository currently contains the **engineering harness** — the contract, docs, architecture tests, evaluation skeleton, CI, and Cursor rules that coding agents must follow. It does **not** yet contain product runtime (ingestion, ClickHouse, detection, Jev, policy, investigation, or UI).

**Jev decides. Code routes. LLM explains. Agent investigates.**

## Quick start

Requirements: Python 3.12+ (standard library only). Use `python3` if `python` is not on your PATH. GitHub Actions installs `python` via `setup-python`.

```bash
python3 -m unittest discover -s tests -t . -v
python3 scripts/check_architecture.py
python3 scripts/eval_smoke.py
```

There is no application server and no third-party package install for this stage.

## Layout

| Path | Role |
| --- | --- |
| `AGENTS.md` | Primary contract for coding agents |
| `docs/` | Architecture, invariants, decisions, product, tools, skills |
| `tests/architecture/` | Enforce harness constraints |
| `evals/` | Scenario / baseline / benchmark structure + smoke runner |
| `scripts/` | Architecture check and eval smoke entrypoints |
| `.cursor/rules/` | Focused Cursor project rules (not a copy of `AGENTS.md`) |
| `.github/workflows/` | PR checks that can actually run today |

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Invariants](docs/INVARIANTS.md)
- [Decisions](docs/DECISIONS.md)
- [Product](docs/PRODUCT.md)
- [Tools](docs/TOOLS.md)
- [Skills](docs/SKILLS.md)

## Status

Harness bootstrap only. Stage 0 Product Foundation is **not** in this tree and must not be implemented as a side effect of harness work.
