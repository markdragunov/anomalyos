# Stage 6 — Two-Mode Incident Engine and Policy Routing

> **Status:** pending Stage 6 (`STATUS.md`). Lifecycle and correlation rules below are *proposals* awaiting owner decision (OQ-8).

## Objective

Turn candidates and verified decisions into a coherent incident system.

This stage is where Mode A becomes a product workflow.

## Mode A lifecycle

```text
CANDIDATE
   ↓
PREFILTER
   ↓
BURST RANK
   ↓
JEV TRIAGE
   ↓
VERIFIER
   ↓
POLICY
   ├── IGNORE
   ├── DIGEST
   └── INCIDENT
```

### IGNORE
Store enough audit information for later analysis.

### DIGEST
Persist as an operationally interesting but non-pageable item.

### INCIDENT
Create/update a business incident.

Low confidence is not an error. It is a valid route.

## Incident correlation

Deterministically correlate related signals:

```text
payment success ↓
PSP X ↓
Visa ↓
Spain ↓
iOS ↓
```

into one incident when correlation rules support it.

Do not let Jev create arbitrary relationships.

## Incident object

Include:
- incident_id
- title
- category
- status
- severity
- started_at
- detected_at
- last_updated_at
- affected_dimensions
- estimated_impact
- confidence
- evidence_ids
- hypotheses
- linked_anomalies
- investigation_status
- route_source
- policy_version

## Lifecycle

```text
DETECTED
INVESTIGATING
ACKNOWLEDGED
ESCALATED
RECOVERING
RESOLVED
```

MVP:
- AI cannot autonomously mark a business incident resolved.
- Recovery can be detected and represented, but final closure requires deterministic workflow/human confirmation.

## Impact

Estimate:
- affected transactions
- affected revenue
- revenue/hour
- affected customers
- affected subscriptions

Every value must be labeled:
- OBSERVED
- ESTIMATED

## Decision audit

Store:
- why candidate was ignored
- why digest was created
- why incident was created
- policy version
- evidence IDs
- Jev decision ID

## Acceptance

Build tests proving:
- same candidate routes deterministically under same policy
- low confidence becomes digest/ignore rather than unsafe action
- correlated anomalies deduplicate
- recovery is represented
- incident creation is auditable

## Amendments

### Lifecycle (proposal, OQ-8)

`docs/ARCHITECTURE.md` lists `candidate → open → investigating → awaiting_decision → resolved | dismissed` (+ `recovered` signalled by detection); this spec lists `DETECTED … RESOLVED`. Proposal — one set, explicit transitions, only a human actor may reach a terminal state:

| From | To | Actor |
|---|---|---|
| `DETECTED` | `INVESTIGATING` | system (Mode B started) |
| `DETECTED`, `INVESTIGATING` | `ACKNOWLEDGED` | human |
| `ACKNOWLEDGED`, `INVESTIGATING` | `ESCALATED` | human, or deterministic policy rule |
| any open state | `RECOVERING` | system (detection signals recovery) |
| `RECOVERING` | `INVESTIGATING` | system (signal returns; no flapping close) |
| any open state, `RECOVERING` | `RESOLVED`, `DISMISSED` | **human only** |

AI never moves an incident to a terminal state. Every transition is logged with actor and reason.

### Correlation rules (deterministic)

Two signals merge into one incident only if **all** hold: (1) their windows overlap or are within a configured gap; (2) their cohorts are nested (one is a refinement of the other) along approved dimension chains; (3) the metric families are compatible. Otherwise they stay separate incidents, optionally linked as `related_to`. Jev never creates a relationship.

### Required negative test

The DE campaign and the iDEAL outage overlap in time and both lower global approval, but are unrelated: they must **not** merge into one incident. A merge here fails the build.

### Impact

Ground truth carries counterfactual `true_impact`; `ESTIMATED` impact (expected minus observed) is compared with it in the benchmark. Every value stays labelled `OBSERVED` or `ESTIMATED`.
