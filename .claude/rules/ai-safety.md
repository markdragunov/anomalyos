---
paths:
  - "src/pulseos/jev/**/*.py"
  - "src/pulseos/policy/**/*.py"
  - "src/pulseos/incidents/**/*.py"
  - "src/pulseos/incident/**/*.py"
  - "src/pulseos/investigation/**/*.py"
  - "docs/specs/06_JEV_INTELLIGENCE.md"
  - "docs/specs/07_INCIDENT_ENGINE.md"
  - "docs/specs/08_INVESTIGATION_AGENT.md"
---

# AI safety

**Jev decides. Code routes. LLM explains. Agent investigates.** Do not invert this.

| Component | Constraint |
|---|---|
| Jev | Pure decision function: typed state in, typed answers out; no I/O or side effects in the decision contract (`INV-004`; the hosted-model call lives in a transport adapter with replay, ADR-024) |
| Jev | Never calls tools, agents or APIs (`INV-005`) |
| Policy engine | Deterministic: same inputs + same policy version ⇒ same disposition; no model call inside (`INV-006`) |
| Investigation agent | Read-only in V2; typed, allow-listed tools only (`INV-007`, ADR-019) |
| LLM | Explains only validated evidence with stable IDs; cites them (`INV-008`, ADR-027) |
| Evidence | Stable identifiers across replay (`INV-009`) |
| Agent execution | Budgets for tokens, tool calls, wall-clock and fan-out; exceeding one is a hard stop (`INV-011`) |
| Ground truth | Never readable by detection, decision or investigation code (`INV-015`) |
| Remediation | No irreversible action without human confirmation (`INV-012`) |

If a change would break any row, stop, name the invariant and propose an ADR instead of working around it.
References: `docs/INVARIANTS.md`, `docs/specs/06`–`08` with their amendments, ADR-018, ADR-019, ADR-024, ADR-027.
