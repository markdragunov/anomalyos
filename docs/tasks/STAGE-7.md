# Task: Stage 7 — Investigation agent, Mode B: bounded, read-only, cited (spec 08)

Prepared: 2026-10-06  
Status: **Approved** by the owner (2026-10-06). Stage 7 decisions start at ADR-049  
Branch: `stage-7-investigation` (from `main` after PR #12)

## Read first

`AGENTS.md` → `docs/specs/08_INVESTIGATION_AGENT.md` (with amendments) → `docs/INCIDENTS.md` → `docs/DECISIONING.md` →
`docs/COHORTS.md` → `docs/ARCHITECTURE.md` (§ 8 Investigation agent, § AI boundaries) → `docs/DECISIONS.md` (ADR-018,
ADR-019, ADR-024, ADR-027, ADR-033, ADR-039 … ADR-042, OQ-1) → `docs/INVARIANTS.md` (INV-001, INV-002, INV-005,
INV-007, INV-008, INV-009, INV-010, INV-011, INV-012, INV-013, INV-015, INV-016).

Work in phases. Stop and report at every ⛔ Gate. Every report: what changed · decisions (ADR ids) · tests (passed /
failed / skipped, with reasons) · measurements · open questions · whether the next phase is authorized.

---

## Purpose

For an open incident, answer **"what actually happened, what evidence supports or contradicts each hypothesis, and what
should a human check next?"** — with a deterministic, read-only, budgeted loop. **The loop is code** (ADR-019 option 3):
code builds a small state and a ranked shortlist of next steps with closed-set arguments; Jev chooses among them (or
`stop`) with a confidence gate; code validates and executes the read-only tool and attaches the result as evidence.
The final report is rendered by a **template explainer** behind a port and passes a **citation validator** (ADR-027,
INV-008). The agent never changes billing state and never closes an incident.

## What Stage 7 inherits

- **Stage 6:** incidents with linked candidates, Stage 4 loci and analyses, one-anchor impact (lost revenue withheld),
  an append-only lifecycle; `DETECTED → INVESTIGATING` is reserved for "Mode B started". Known gaps: 0.8–1.0 duplicates
  per covered record; correlation not cleanly validated (HELDOUT, Stage 9).
- **Stage 4 / 5:** bounded evidence bundles with stable evidence ids; first-look labels 63 %, 78–80 % at + 6 h.
- **Jev is not evaluated** (no provider access, OQ-1): every Jev step runs on the deterministic fake or is replaced by
  the deterministic control; nothing in this stage may claim Jev quality.
- **Tools without a data source:** `get_deployments`, `check_psp_status` (the side files `deployments.json` /
  `psp_status.json` were postponed to Stage 7 by ADR-033). `search_similar_incidents` now has one: the Stage 6
  incident history of the same run.

Do not repair Stages 3–6 inside Stage 7; record limitations and propose changes separately.

---

# Phase 0 — Design ⛔ Gate 0

No product implementation before explicit owner approval. The design note covers:

## 0.1. Scope of an investigation

Input: one incident (id, current snapshot, linked candidates, their latest analyses and decisions), the run, a
budget. Output: an `Investigation` — steps, evidence, hypotheses, stop reason, report — stored append-only. When an
investigation starts, the incident gets the system transition `DETECTED → INVESTIGATING` (ADR-028); nothing else in
the incident changes except attached evidence. Which incidents are investigated (all, or by severity / route) — proposal.

## 0.2. Stage A — evidence narrowing

Bounded chunks of analytical evidence (cohort rows, metric windows, related metrics) → importance per chunk → recurse
into selected chunks → stop at a leaf size; chunk size, depth and leaf budget configurable. Importance by Jev (`noul`
per chunk, fake now) **and** by the deterministic control (top-k by contribution). Define the chunk types, their
evidence ids and the size bounds.

## 0.3. Stage B — the loop (ADR-019 option 3)

OBSERVE → HYPOTHESES → SHORTLIST → CHOOSE → EXECUTE → EVALUATE → UPDATE → STOP / CONTINUE. Define: the state (typed,
bounded, no free text); the shortlist builder (≤ N steps, deterministically ranked, closed-set arguments only); the
question set for the step choice (`choice` over the shortlist + `stop`) and the confidence gate; the deterministic
control (take the top-ranked step); the **disconfirming-check rule** (each step set includes at least one check that
could contradict the leading hypothesis — enforced by the loop); repeated-query prevention (a step with the same tool and
arguments is never run twice).

## 0.4. Tools (typed, read-only, timed, audited)

`get_incident`, `get_metric_history`, `breakdown_by_dimension`, `compare_cohorts`, `get_related_metrics`,
`calculate_impact`, `read_evidence`, `search_similar_incidents` (Stage 6 history of the run). Each: typed input with
closed-set arguments, typed bounded output, a stable evidence id, a timeout, an audit row; ClickHouse only through
`metrics.compute` and the Stage 6 tables (INV-002, INV-003). No mutation tool exists (INV-007; an architecture test
fails on mutation names in the registry).

**Owner decision (ADR-033 postponed it to here):** add simulator side files `deployments.json` and `psp_status.json`
(generated independently of ground truth, with decoys and honest signals, without changing event digests) so
`get_deployments` and `check_psp_status` get a data source — or keep both unregistered in Stage 7.

## 0.5. Hypotheses

Hypotheses only from the closed cause vocabulary v1 (`category` choices); record: id, cause, statement template,
supporting and contradicting evidence ids, support probability (Jev `noul` per hypothesis, fake now), confidence,
status (`open`, `supported`, `contradicted`, `insufficient_evidence`), next question. Contradicting evidence is kept,
never dropped. Language: "consistent with", "evidence against", "insufficient evidence" — never causal certainty. The
deterministic control's hypothesis scoring (e.g. metric family × locus dimensions → cause prior) — define it.

## 0.6. Budgets and stop conditions (INV-011)

Hard caps with numeric defaults: tool calls, Jev calls, wall-clock (as an explicit input, no clock reads in the loop's
decisions), evidence items, repeated queries. Stop on: sufficient evidence, strong contradiction, false-positive
conclusion (`normal_variation` supported), budget exhausted, tool / model failure, low confidence (hand off to a human).
Failure returns the partial structured state, never a crash.

## 0.7. Explanation (ADR-027 option 3, INV-008, INV-013)

An `Explainer` port; the template explainer renders the report from typed state: incident summary, narrowed evidence,
hypotheses with supporting / contradicting evidence ids, timeline, tool trace. Every claim carries evidence ids and an
epistemic label (observed / inferred / estimated / recommended). The citation validator rejects unknown ids and
uncited claims before a report is returned. No generative model in this stage.

## 0.8. Storage and audit

Append-only ClickHouse tables (proposal): `investigation_steps` (one row per step: state hash, shortlist, choice and its
source — Jev / fake / control —, tool, arguments, evidence id, budget counters, timestamps) and `investigation_reports`
(hypotheses, stop reason, report, validation result). DDL in an ADR. Package names: `investigation` and `explanation`
(both in the stage guard) — confirm.

## 0.9. Evaluation protocol — fixed before results

DEV seeds only; HELDOUT untouched; defaults tuned (if at all) on seeds 1–10, reported on 11–20. Per ground-truth
incident with an engine incident (Stage 6 main record):
- **hypotheses:** true cause at top-1 / top-3 of the final ranking; contradicting evidence recorded when the truth is
  not the leader;
- **narrowing:** the root-cause locus (or an equivalent, ADR-038 rules) present in the narrowed evidence;
- **false-positive incidents:** share concluded `normal_variation` / `insufficient_evidence` instead of a cause;
- **safety:** citation validity 100 %, zero mutation calls, zero repeated queries, budgets never exceeded,
  disconfirming check present in every investigation;
- **cost:** steps, tool calls, Jev calls, evidence items, wall-clock per investigation.
Systems compared on the same incidents: **deterministic control (top-k, no Jev)** as the reference, and the fake client
only as a pipeline check. Jev itself: blocked — reported as such.

## 0.10. Scope boundary

Out: generative explanations (a later ADR), UI and API (Stage 8), notifications, any write to billing state or incident
closure, Jev live calls, HELDOUT, new dependencies without an ADR, free-text inputs from data into any state.

---

# Phase 1 — Implementation (after Gate 0 approval)

`src/pulseos/investigation/` (narrowing, state, shortlist, loop, tools, hypotheses, budgets, storage) and
`src/pulseos/explanation/` (port, template explainer, citation validator), or the layout approved at Gate 0; remove
them from the stage guard in the same change. Tests: every tool typed and read-only (architecture test against
mutation names), budgets hard-stop, repeated-query prevention, disconfirming-check rule, contradicting evidence kept,
low-confidence hand-off, failure → partial state, citation validator rejects unknown ids and uncited claims, no free
text in any state, ground-truth isolation, deterministic replay, end-to-end on a simulated world.

# Phase 2 — Evaluation on DEV seeds ⛔ Gate 1

The 0.9 protocol on 20 seeds × realism v1/v2; the deterministic control and the fake pipeline reported separately; per
scenario kind; "Jev evaluation: blocked" stated.

# Phase 3 — Documentation and PR

`docs/INVESTIGATION.md`, ADRs for Gate 0 / Gate 1 (Stage 7 starts at ADR-049, owner decision 2026-10-06: ADR-043 …
ADR-045 stay reserved by Stage 6, ADR-046 … ADR-048 are taken), stage tables, PR `stage-7-investigation` → `main`,
not merged.

---

# Acceptance

- [ ] Gate 0 design approved; decisions recorded as ADRs (tools, budgets, storage DDL, side-files decision).
- [ ] The agent reads only through typed, bounded, read-only tools; no mutation tool; no arbitrary SQL; bounded evidence.
- [ ] Loop is code; Jev (fake now) only chooses among a shortlist; low confidence hands off; no repeated queries.
- [ ] Every investigation contains a disconfirming check; contradicting evidence is preserved.
- [ ] Budgets hard-stop; failures return partial structured state.
- [ ] Every report passes the citation validator; claims carry epistemic labels; no incident closure.
- [ ] DEV report: control vs fake pipeline, hypotheses top-1 / top-3, narrowing, safety, cost; HELDOUT untouched.
- [ ] Harness and product tests green locally and in CI; PR open, not merged.
