# Stage 5 — Jev Decision Layer, Verifier and Decision Policy

> **Status:** pending Stage 5 (`STATUS.md`). Aligned to ADR-018, ADR-019, ADR-024.

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

Mode B uses deterministic narrowing first (spec 05 already ranks cohorts by contribution). Jev is asked about a chunk or a next step only through single-judgment questions over a small state, one question per judgment (ADR-018):
- importance `noul` (per item)
- best item among a bounded shortlist `choice`

Question *batching* means several independent questions in one request about the same small state; it does not mean one question that bundles judgments, and it must not push the state past its size budget. Whether hierarchical chunk ranking by Jev beats the deterministic top-k control is a benchmark question (spec 10), not an assumption.

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

## Amendments (ADR-018, ADR-019, ADR-024)

- **Jev is a hosted typed-question model**, reached through a `JevClient` port. The decision contract is pure; a transport adapter performs the call; tests and evaluation replay recorded raw responses. A state hash alone does not reproduce a non-deterministic model, so the audit record stores the raw response, the question-set version and the *returned* model version.
- **One question, one judgment.** No arithmetic, counting or date comparison in Jev: code computes values and passes numbers or named buckets (`docs/DATA_MODEL.md`, semantic buckets). States are small and filtered per question set. No free text from data enters a state.
- **What each primitive returns.** `noul` → probability only. `choice` → a probability per option plus a confidence. `score` → an ordinal value plus a confidence. Therefore `severity_confidence` in the policy example exists only for a `choice`/`score` severity question; for a `noul` the gate uses its probability. Thresholds are per question and per risk level.
- **Vocabularies.** `category` and hypotheses use the closed cause vocabulary in `docs/DATA_MODEL.md` (v1) — the same labels as ground-truth `true_cause`. Routes (accepted, ADR-028): `INCIDENT` = ground-truth `incident`, `DIGEST` = `watch`, `IGNORE` = `suppress`. Severity (ADR-028): the single scale is `none / low / medium / high / critical`; Jev's severity `choice` ranges over `low…critical` (`none` = not an incident, carried by the incident `noul`). `P1/P2/P3` in the examples above is only a UI label, mapped in Stage 8; it never appears in Jev state, policy or ground truth.
- **Thresholds are tuned on `DEV_SEEDS` only** and frozen before any held-out evaluation (spec 10).
- **Failure.** On model error, timeout or an unverifiable answer the route is `DIGEST` (accepted, ADR-028) (uncertainty becomes digest, never a page), the failure is audited, and there is no retry loop.
- **Isolation.** No ground truth, `run_id` or `scenario_id` in any state.
