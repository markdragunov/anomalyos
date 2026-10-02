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
| `03_CLICKHOUSE_ANALYTICS` | Stage 2 | Stage 2 (part), then later | `stage-2-metrics` | **Next.** Stage 2 = normalized events + metrics (`docs/tasks/STAGE-2.md`; ADR-022 accepted, ADR-025 normalization, ADR-026 grain). The typed query layer and evidence objects follow once metrics exist. |
| `04_ANOMALY_DETECTION` | Stage 3 | Stage 3 | not started | Pending Stage 2. |
| `05_COHORT_INTELLIGENCE` | Stage 4 | Stage 4 | not started | Pending Stage 3. |
| `06_JEV_INTELLIGENCE` | Stage 5 | Stage 5 | not started | Pending. Needs the `JevClient` port design (ADR-024) and question-set design (OQ-5). |
| `07_INCIDENT_ENGINE` | Stage 6 | Stage 6 | not started | Pending. Lifecycle states: owner decision (OQ-8). |
| `08_INVESTIGATION_AGENT` | Stage 7 | Stage 7 | not started | Pending. Aligned to ADR-019 option 3; three tools have no data source yet. |
| `09_INCIDENT_UI` | Stage 8 | Stage 8 | not started | Pending. No API contract spec yet; a second stack (Next.js) needs an ADR. |
| `10_EVALUATION_BENCHMARK` | Stage 9 | Stage 9 | not started | Pending; depends on the simulator delta (randomised calendar, `oracle_detectable_at`). |
| `11_FINAL_REVIEW` | Final | Final | n/a | After all stages. |

## Open points the specs depend on (owner decisions)

- OQ-5 Jev question-set design · OQ-6 severity vocabulary · OQ-7 where mutable incident state lives · OQ-8 incident lifecycle.
- Whether "V1 = Mode A, V2 = plus read-only agent" is the intended split: `AGENTS.md` and `docs/ARCHITECTURE.md` use "V2" for the whole current product generation (agent read-only *in V2*) and do not define a V1. Until decided, specs 08 and the investigation part of 09 are marked only as "Mode B / built after Mode A".
