# Stage 0 — Foundation and Two-Speed Architecture

> **Status: DONE (Stage 0).** Delivered in the repository (`src/pulseos/config.py`, `clickhouse.py`, Docker Compose, CI, docs). Do not re-run this brief. Differences from the text below: `CLAUDE.md` is a thin pointer to `AGENTS.md` (ADR-003, ADR-023) and must not be overwritten; existing docs are *updated, never overwritten*; layer packages (`detection/`, `decision/`, …) are created only in the stage that implements them; every new dependency, including FastAPI and Next.js, needs an ADR first (`AGENTS.md`).

Use `00_MASTER_CONTEXT.md` as authoritative context.

## Objective

Create the engineering foundation for a two-mode AI Incident Intelligence system.

Before coding, audit:
- repository
- git state
- Python/Node versions
- existing dependencies
- tests
- Docker
- docs
- existing architecture

Do not overwrite existing work blindly.

## Create

```text
src/pulseos/
  domain/
  analytics/
  detection/
  decision/
  policy/
  incident/
  investigation/
  explanation/
  api/

tests/
docs/
scripts/
data/
```

Create:
- Python project configuration
- Docker Compose with ClickHouse
- environment configuration
- test setup
- lint/type-check setup
- `CLAUDE.md`
- docs listed below

## Required docs

```text
docs/
  PRODUCT.md
  ARCHITECTURE.md
  DATA_MODEL.md
  DECISIONS.md
  TESTING.md
  MODE_A_REALTIME.md
  MODE_B_INVESTIGATION.md
```

## Architecture requirement

Document this explicitly:

```text
                    EVENTS
                       ↓
                  ClickHouse
                       ↓
             deterministic analytics
                       ↓
             ┌─────────┴─────────┐
             ↓                   ↓
          MODE A               MODE B
        real-time             investigation
             ↓                   ↓
        prefilter             chunking
             ↓                   ↓
        Jev triage             Jev drilldown
             ↓                   ↓
          verifier             evidence
             ↓                   ↓
           policy              agent
       ┌─────┼─────┐            ↓
     IGNORE DIGEST INCIDENT     Jev
                                  ↓
                                 LLM
                                  ↓
                              explanation
```

## Acceptance criteria

- application starts
- ClickHouse starts
- health check works
- tests run
- docs define Mode A and Mode B
- no AI dependency is required for deterministic tests
- repository is ready for Stage 1
