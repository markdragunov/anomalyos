# Stage 7 — Investigation Agent: Bounded Mode B

## Objective

Build a read-only Investigation Agent inspired by JevOps Mode B.

The agent answers:
> What actually happened, what evidence supports each hypothesis, and what should a human investigate next?

It must not behave like an unrestricted browser agent.

## Two-stage investigation

### Stage A — hierarchical evidence narrowing

Given a time window or incident:
1. divide analytical evidence into bounded chunks
2. ask Jev which chunks are important
3. recurse into important chunks
4. stop at bounded leaf size
5. cap total leaves

Conceptual:

```text
incident window
   ↓
40-ish evidence items/chunk
   ↓
Jev importance
   ↓
selected chunks
   ↓
subdivide ×4
   ↓
repeat
   ↓
small evidence set
```

Do not copy numbers blindly; make chunk size, depth and leaf budgets configurable.

### Stage B — investigation agent

The agent receives only the narrowed evidence set and can call typed read-only tools.

## Tools

Implement:

```text
get_incident
get_metric_history
breakdown_by_dimension
compare_cohorts
get_related_metrics
search_similar_incidents
get_deployments
check_psp_status
calculate_impact
read_evidence
```

Every tool:
- typed input
- typed output
- permission boundary
- timeout
- audit event

## Agent loop

```text
OBSERVE
 ↓
FORM HYPOTHESES
 ↓
SELECT NEXT EVIDENCE
 ↓
TOOL CALL
 ↓
EVALUATE
 ↓
JEV DECISION
 ↓
UPDATE HYPOTHESES
 ↓
STOP or CONTINUE
```

Jev can decide:
- hypothesis support
- evidence sufficiency
- investigation priority
- whether contradictory evidence exists
- whether more investigation is warranted

The agent chooses the actual tool call.

## Hypothesis record

```text
hypothesis_id
statement
supporting_evidence_ids
contradicting_evidence_ids
support_probability
confidence
status
next_question
```

Do not claim causal certainty when evidence is correlational.

Use language such as:
- likely cause
- consistent with
- evidence against
- insufficient evidence

## Budgets

Enforce:
- max tool calls
- max wall-clock duration
- max Jev calls
- max evidence items
- max repeated query count

Stop on:
- sufficient evidence
- strong contradiction
- false-positive conclusion
- budget exhaustion
- tool/model failure

## LLM explanation

Only after evidence is validated.

LLM receives:
- incident summary
- selected evidence
- hypothesis records
- timeline
- tool trace

LLM must:
- cite evidence IDs
- distinguish observed/inferred/estimated/recommended
- never invent evidence
- never change incident state

Validate citations before returning the report.

## Honest degradation

If Jev or LLM fails:
return partial structured investigation state rather than crash.

## Acceptance

Demonstrate:
- agent does not inspect arbitrary raw data
- evidence set is bounded
- repeated tool loops are prevented
- contradictory evidence is preserved
- final explanation cites validated evidence
