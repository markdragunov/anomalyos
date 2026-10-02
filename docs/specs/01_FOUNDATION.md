# Stage 0 — Foundation and Two-Speed Architecture

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
src/anomalyos/
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
