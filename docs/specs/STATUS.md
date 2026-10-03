# Spec status

Maps each spec file to a repository stage, branch and status. The file headings ("Stage N") follow the
original build order and **match** the repository stage numbers, with one exception: spec `03` is wider than
repository Stage 2 (see below). The commit list that used to be in `README.md` (`001 foundation` …) was off by
one against both and is replaced by this table. Where a spec conflicts with `docs/DECISIONS.md`, the ADRs win.

| Spec | Heading says | Repository stage | Branch | Status |
|---|---|---|---|---|
| `00_MASTER_CONTEXT` | context | all | n/a | Active. Aligned to ADR-018/019/024/027 (see its banner). |
| `01_FOUNDATION` | Stage 0 | Stage 0 | `consolidate-repo` → `main` | **Done** (config, ClickHouse dev service, health check, docs, CI). |
| `02_BILLING_SIMULATION` | Stage 1 | Stage 1 | `consolidate-repo` → `main` (+ `sim-1.1`) | **Done** (sim-1.0.0). Remaining delta: `docs/tasks/SIMULATOR-FIXES.md`. |
| `03_CLICKHOUSE_ANALYTICS` | Stage 2 | Stage 2 (part), then later | `stage-2-metrics` | **Stage 2 built** (normalized events + metrics, branch `stage-2-metrics`; ADR-022, 025, 026; `docs/METRICS.md`). The typed query layer and evidence objects follow. |
| `04_ANOMALY_DETECTION` | Stage 3 | Stage 3 | `stage-3-detection` | **Built** (ADR-034, ADR-035; `docs/DETECTION.md`). |
| `05_COHORT_INTELLIGENCE` | Stage 4 | Stage 4 | `stage-4-cohorts` | **Built** (ADR-037, ADR-038; `docs/COHORTS.md`). The daily cohort sweep exists but is off in Mode A. |
| `06_JEV_INTELLIGENCE` | Stage 5 | Stage 5 | not started | Pending. Needs the `JevClient` port design (ADR-024) and question-set design (OQ-5). Severity scale fixed (ADR-028). |
| `07_INCIDENT_ENGINE` | Stage 6 | Stage 6 | not started | Pending. Lifecycle and storage decided (ADR-028). |
| `08_INVESTIGATION_AGENT` | Stage 7 | Stage 7 | not started | Pending. Aligned to ADR-019 option 3; three tools have no data source yet. |
| `09_INCIDENT_UI` | Stage 8 | Stage 8 | not started | Pending. No API contract spec yet; a second stack (Next.js) needs an ADR. |
| `10_EVALUATION_BENCHMARK` | Stage 9 | Stage 9 | not started | Pending; depends on the simulator delta (randomised calendar, `oracle_detectable_at`). |
| `11_FINAL_REVIEW` | Final | Final | n/a | After all stages. |

## Open points the specs depend on (owner decisions)

- OQ-5 Jev question-set design (the only one left).
- Closed by ADR-028: OQ-6 severity, OQ-7 incident state storage, OQ-8 lifecycle, routing/fallback/merge rule, and versions: there is no V1/V2 split; "V2" keeps the `AGENTS.md` meaning and "Mode A first" is only the stage order.
