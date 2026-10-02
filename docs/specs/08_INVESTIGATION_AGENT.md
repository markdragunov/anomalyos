# Stage 7 — Investigation Agent: Bounded Mode B

> **Status:** pending Stage 7 (`STATUS.md`). Aligned to ADR-019 option 3 and ADR-027. Mode B, built after Mode A; the V1/V2 split is an open question (`STATUS.md`).

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

## Amendments (ADR-019 option 3, ADR-027)

- **Who picks the tool.** The loop is code. Each step, code builds a small state and a bounded, deterministically ranked shortlist of next steps (top-k by contribution, closed-set arguments); Jev chooses among them (or `stop`) with a confidence gate; code validates and executes read-only. Free-form tool arguments do not exist; low confidence stops and hands off to a human. "The agent chooses the tool call" in the text above means this loop.
- **Benchmark control.** Spec 10 includes "deterministic top-k, no Jev" so the value of both stage A (hierarchical Jev narrowing) and the Jev step choice can be disproved.
- **Explanation.** An explainer behind a port, templated first; a generative model only after a measured gap, under its own ADR (ADR-027). The citation validator (`INV-008`) is built first.
- **Prompt injection.** Only typed fields enter any model state or prompt; free text from data (metadata, failure messages) is excluded or screened.
- **Tools without a data source today:** `get_deployments`, `check_psp_status`, `search_similar_incidents`. Mark them "no data source" until the simulator provides deployments, PSP status and incident history (`SIMULATOR-FIXES`, phase 5); until then they are not registered.
- **Budgets need defaults.** `max tool calls`, `max Jev calls`, `max wall-clock`, `max evidence items`, `max repeated queries` get numeric defaults and a hard-stop test; the values are an owner decision made with the first measurements.
- **Isolation.** No ground truth for the agent.
