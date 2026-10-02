# Stage 5 — Jev Decision Layer, Verifier and Decision Policy

## Objective

Implement the central Jev decision layer using the pattern:

> **Jev decides. Code verifies/routes. The LLM explains.**

Do not turn Jev into a generic chatbot.

## Modes

Support two decision families.

### Mode A — Triage

Given a compact `EvidenceBundle`, ask in one request where useful:

```text
is_incident        noul
severity            choice
category            choice
needs_human        noul
novelty             score
recovery            noul
```

Do not assume every field is needed in production; measure which improve decisions.

### Mode B — Drilldown

Given bounded chunks:
- importance `noul`
- best chunk `choice`
- optionally `contains_incident` `noul`

Batch chunk questions into a single request where cardinality permits.

## Typed state

State must contain:
- anomaly summaries
- cohort evidence
- controls
- metric history
- impact
- business context
- explicit evidence IDs

Never raw unbounded logs.

## Verifier

Validate:
- schema
- option membership
- numeric ranges
- probability consistency
- required evidence IDs
- state hash
- model/version metadata
- maximum cardinality
- decision freshness

Verifier does not invent missing evidence.

## Policy

Policy is deterministic code.

Example conceptual policy:

```text
Jev result
   ↓
Verifier
   ↓
Policy
   ├── IGNORE
   ├── DIGEST
   └── INCIDENT
```

Illustrative conditions:

```python
if not verified:
    route = "DIGEST"

elif incident_probability < INCIDENT_FLOOR:
    route = "IGNORE"

elif needs_human < HUMAN_FLOOR:
    route = "DIGEST"

elif severity_confidence < SEVERITY_FLOOR:
    route = "DIGEST"

else:
    route = "INCIDENT"
```

Thresholds must be configuration and benchmarked.

## Decision audit

Persist:

```text
decision_id
mode
question_set
state_hash
model
model_version
answers
probabilities
confidence
evidence_ids
verifier_status
policy_version
route
created_at
```

## Important limitation

Jev can be wrong inside its schema.

Never claim:
- Jev is causal
- Jev is always calibrated
- Jev is always better than an LLM

The benchmark must test these claims on AnomalyOS data.

## Failure behavior

If Jev fails:
- no silent retry loop
- structured model_error
- safe fallback route
- audit the failure

For high-volume Mode A, model failure should not create an unsafe page.
