# Stage 6 — Two-Mode Incident Engine and Policy Routing

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
