# Stage 8 — Incident Intelligence UI

## Objective

Build a UI that makes the two-speed architecture visible.

This is an operational incident workspace, not a generic BI dashboard.

## Stack

Use:
- Next.js
- TypeScript
- lightweight charting/component libraries
- existing project conventions where possible

Avoid unnecessary dependencies.

## Screens

### 1. Operations overview

Show:
- active incidents
- digests
- ignored candidate count
- severity
- duration
- estimated impact
- confidence
- affected cohorts
- detection latency

### 2. Incident detail

Show:
- incident summary
- timeline
- primary metric
- baseline vs current
- cohort decomposition
- controls
- impact
- evidence
- hypotheses
- policy route

### 3. Decision trace

Make the architecture inspectable:

```text
Candidate
 ↓
Prefilter
 ↓
Burst ranking
 ↓
Jev decision
 ↓
Verifier
 ↓
Policy
 ↓
INCIDENT / DIGEST / IGNORE
```

Show:
- decision ID
- model/version
- probabilities
- confidence
- evidence IDs
- verifier status
- policy version

### 4. Investigation workspace

Show:
- agent state
- current hypotheses
- supporting evidence
- contradicting evidence
- tool calls
- Jev decisions
- budget remaining
- open questions
- final explanation

### 5. Hierarchical drilldown

Visually expose:

```text
large time window
 ↓
chunks
 ↓
selected chunks
 ↓
leaf evidence
 ↓
explanation
```

### UX evidence labels

Every statement must be visually classified:

- OBSERVED
- INFERRED
- ESTIMATED
- RECOMMENDED

Example:

```text
OBSERVED
PSP X approval rate fell 11.3pp.

INFERRED
The degradation is concentrated in Spain + Visa.

ESTIMATED
€184k GMV/hour may be affected.

RECOMMENDED
Investigate PSP X authorization API and routing.
```

Do not present inference as fact.

## Acceptance

The UI should make it possible to answer in under one minute:
- what happened?
- how large is it?
- where?
- why do we think that?
- what has the system checked?
- what remains unknown?
