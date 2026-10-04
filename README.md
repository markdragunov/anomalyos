# AnomalyOS

AI-native Billing & Payments Incident system — Research prototype.

AnomalyOS detects meaningful anomalies in billing and payment systems, decides whether an
anomaly is a real business incident, isolates the affected cohorts and likely causes,
estimates impact, and supports an evidence-backed investigation that a human closes.

**Jev decides. Code routes. LLM explains. Agent investigates.**

> **Status: Stage 5 — decision layer (Jev not yet evaluated).** Deterministic Stripe-shaped billing/payments simulator
> (≈1.37M events per run) with machine-readable ground truth; a normalized event layer (`events_norm`)
> and versioned metrics computed in ClickHouse ([docs/METRICS.md](docs/METRICS.md)); first-look anomaly detection
> ([docs/DETECTION.md](docs/DETECTION.md)); cohort localization with bounded evidence bundles
> ([docs/COHORTS.md](docs/COHORTS.md)); a first-look Mode A pipeline — JevState, verifier, deterministic policy, audit
> ([docs/DECISIONING.md](docs/DECISIONING.md)) — running on a fake client until Jev access exists. No incident engine,
> agent or UI yet.
> Stage table: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#stage-status).

This repository is the single home of both the **coding harness** (contract, invariants,
architecture tests, eval skeleton, CI, Claude Code configuration in `.claude/`) and the **product runtime**, built stage
by stage ([ADR-023](docs/DECISIONS.md)).

## Quickstart

Product: Python 3.11+ and Docker (Compose v2). Harness: Python 3.12, standard library only.

```bash
# harness (no install)
python3 -m unittest discover -s tests/architecture -t . -v
python3 scripts/check_architecture.py
python3 scripts/eval_smoke.py

# product
cp .env.example .env                 # local-only credentials; .env is gitignored
docker compose up -d clickhouse      # waits on /ping healthcheck
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
set -a; source .env; set +a          # export config to the Python process
anomalyos doctor                     # -> clickhouse: OK
pytest                               # unit tests (integration auto-skipped)
ANOMALYOS_RUN_INTEGRATION=1 pytest   # + live ClickHouse tests
```

Generate and load a synthetic world:

```bash
anomalyos-sim generate --seed 42 --out data/run_42 --validate   # ~1.37M events, ~1.5 min
anomalyos-sim load --in data/run_42 --replace                   # needs .env exported
anomalyos normalize --in data/run_42 --replace                  # builds events_norm (metrics read only this)
```

`anomalyos doctor` exit codes: `0` healthy, `1` ClickHouse unreachable/unauthorized, `2` invalid config.
Stop / reset: `docker compose down` (keeps data), `docker compose down -v` (wipes the volume).

## Layout

| Path | Role |
| --- | --- |
| `AGENTS.md` | The contract for coding agents (`CLAUDE.md` points to it) |
| `src/anomalyos/` | Runtime: `config`, `clickhouse` (health), `simulation/` (world, scenarios, ground truth, loader) |
| `tests/architecture/` | Harness constraints (stdlib unittest) |
| `tests/unit/`, `tests/integration/` | Product tests (pytest; integration is opt-in, needs ClickHouse) |
| `evals/`, `scripts/` | Scenario/baseline/benchmark structure, architecture check, eval smoke |
| `docs/` | Architecture, invariants, decisions, product, data model, simulation, testing, tools, skills |
| `docs/specs/` | Build specs per stage (target design) |
| `docs/tasks/` | Current task brief |
| `docker-compose.yml` | Local ClickHouse only |
| `CLAUDE.md`, `.claude/` | Claude Code entry point (imports `AGENTS.md`), path-scoped rules, Skills conventions, minimal permissions |
| `.github/workflows/` | `harness.yml` and `ci.yml` |

## Docs

[Architecture](docs/ARCHITECTURE.md) · [Invariants](docs/INVARIANTS.md) · [Decisions](docs/DECISIONS.md) ·
[Product](docs/PRODUCT.md) · [Data model](docs/DATA_MODEL.md) · [Simulation](docs/SIMULATION.md) ·
[Testing](docs/TESTING.md) · [Tools](docs/TOOLS.md) · [Skills](docs/SKILLS.md) · [Specs](docs/specs/README.md)
