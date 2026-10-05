# Task: Stage 6 — Incident engine: correlation, lifecycle, re-evaluation (spec 07)

Prepared: 2026-10-04  
Status: **Draft for owner review**  
Branch: `stage-6-incidents` (from `main` after PR #10)

## Read first

`AGENTS.md` → `docs/specs/07_INCIDENT_ENGINE.md` (with amendments) → `docs/DECISIONING.md` → `docs/COHORTS.md` →
`docs/DETECTION.md` → `docs/DATA_MODEL.md` (Incident, routes, severity) → `docs/ARCHITECTURE.md` (§ 7 Incident
engine) → `docs/DECISIONS.md` (ADR-027, ADR-028, ADR-037 … ADR-040) → `docs/INVARIANTS.md` (INV-006, INV-009,
INV-010, INV-012, INV-013, INV-015, INV-016).

Work in phases. Stop and report at every ⛔ Gate. Every report: what changed · decisions (ADR ids) · tests (passed /
failed / skipped, with reasons) · measurements · open questions · whether the next phase is authorized.

---

## Purpose

Turn Mode A decisions into a coherent, auditable incident system: correlate related candidates into one incident,
keep `DIGEST` items, follow each incident through its lifecycle as the underlying episodes evolve, and estimate its
impact — deterministically. **Code correlates and moves state; humans close.** No Jev-created relationships, no
autonomous closure, no generated text (titles and summaries are code templates, ADR-027).

## What Stage 6 inherits

- **Stage 3:** candidates are episodes; `as_of_view` / `concurrent_at` give what was known at any time; recovery is
  signalled at `recovered_at`. Recall 0.89 / 0.87, 0.53 false positives per day.
- **Stage 4:** at first look top-1 exact locus 41–42 %, rate-vs-mix label 57–58 % (82–83 % on the full window);
  known gaps: dimensions inherited from the Stage 3 scope in the locus; attribution between simultaneous incidents;
  impact accuracy (deferred here by ADR-038).
- **Stage 5:** one first-look decision and audit record per promoted candidate; `policy_v1` (not tuned);
  `baseline_v2` (no-Jev control; validation recall at INCIDENT 17 %, 0.30 INCIDENT routes per day).
  **Jev is not evaluated** (no access, OQ-1): with the fake client, routes, severity and category carry no
  information.

Do not repair Stage 3–5 inside Stage 6; record limitations and propose changes separately.

---

# Phase 0 — Design ⛔ Gate 0

No product implementation before explicit owner approval. The design note covers:

## 0.1. Inputs, time and event stream

- Inputs: Stage 3 candidates (episodes), Stage 5 decision records, Stage 4 analyses, and typed **human actions**
  (acknowledge, escalate, resolve, dismiss — with actor and reason; no UI in this stage, a typed command interface
  used by tests and evaluation).
- The engine consumes a **time-ordered event stream** derived as of each moment: candidate detected (first look),
  candidate updated (episode grows), candidate recovered, decision made, human action. Same events and versions ⇒
  same incident history (replay). No clock reads; time is an input.
- Which candidate updates the engine sees (each window close, or fixed checkpoints) and the cost bound.

## 0.2. Route handling

- `IGNORE`: nothing beyond the Stage 5 audit record.
- `DIGEST`: a persisted, non-pageable digest item (what it carries, how digest items of the same underlying change
  are grouped, how a digest item is promoted if a later decision routes `INCIDENT`).
- `INCIDENT`: create or update an incident through the correlation rules.

## 0.3. Correlation rules (refine the ADR-028 working rule)

Two candidates belong to one incident only if **all** hold: (1) windows overlap or are within a configured gap;
(2) cohorts are nested along approved dimension chains (define the chains, including how a global scope relates to
a sub-scope); (3) metric families are compatible (define the compatibility table). Otherwise separate incidents,
optionally `related_to`. Define: merging two existing incidents, never splitting silently, tie-breaks, and why each
link exists (auditable). Jev never creates a relationship.

**Required negative test:** the DE campaign and the iDEAL outage (spec 07) overlap and both lower global approval but
are unrelated — they must not merge. In the simulator this is `correlated_unrelated_anomalies`, parameterized as a
country campaign plus an iDEAL **or** SEPA outage; the test covers every variant, not only DE + iDEAL. Ground
truth's `unrelated_to` lists such pairs; the evaluation counts every merged `unrelated_to` pair as a wrong merge.

## 0.4. Incident object

Spec 07 fields (incident_id, title, category, status, severity, started_at, detected_at, last_updated_at,
affected_dimensions, estimated_impact, confidence, evidence_ids, hypotheses, linked_anomalies, investigation_status,
route_source, policy_version) — define the source of each, how it changes when candidates are added, and the
epistemic label of each value (`OBSERVED` / `ESTIMATED` / `INFERRED`, INV-013). Stable ids across replay.

## 0.5. Lifecycle (ADR-028 transition table)

System transitions (DETECTED on creation; RECOVERING when the linked signals recover; RECOVERING → INVESTIGATING when
a signal returns) with hysteresis against flapping; human transitions through the command interface; escalation by a
deterministic rule (proposal). `DETECTED → INVESTIGATING` needs Mode B (Stage 7) — state how it is handled now.
Only a human reaches `RESOLVED` / `DISMISSED`; every transition is a new append-only row with actor and reason.

## 0.6. Re-evaluation as an episode evolves

First-look Stage 4 labels are weak (57–58 % vs 82–83 %). Options: (a) keep first-look decisions only; (b) re-run
Stage 4 and the Stage 5 decision at defined checkpoints (e.g. episode close, every N windows) and let a later decision
update the incident (severity, category, route upgrade `DIGEST → INCIDENT`; never an automatic downgrade to closed).
Recommend one, with its cost (extra Jev calls when access exists) and what the audit shows.

## 0.7. Impact

Per incident: affected transactions, lost successful payments and revenue (per currency), revenue per hour,
affected customers and subscriptions where available — each `OBSERVED` or `ESTIMATED`, with intervals for estimates,
aggregated without double counting overlapping linked candidates. Compared with ground-truth `true_impact` in
evaluation only.

## 0.8. Storage

Append-only ClickHouse tables (ADR-028): incident state rows, incident ↔ candidate links, digest items, transitions;
current state = latest row per incident. DDL in an ADR. Package name: `incidents` (stage guard lists `incident` and
`incidents`) — confirm.

## 0.9. Burst ranking (spec 04 / 07)

Spec 07 places a burst-rank step before Jev triage (a bounded group of nearby candidates, ranked to cap Jev calls).
Implement now (deterministic, interface only) or defer — proposal.

## 0.10. Evaluation protocol — fixed before results

DEV seeds only; HELDOUT untouched; thresholds (gap, hysteresis, checkpoints) tuned on seeds 1–10, reported on 11–20.
Incident level, against ground-truth incident records (`incident_id`, `unrelated_to`):
- incident recall (ground-truth incidents covered by ≥ 1 engine incident), duplicates per ground-truth incident,
  purity (share of an engine incident's candidates that belong to its main ground-truth incident), wrong merges
  (merged `unrelated_to` pairs), the campaign + outage negative case in all its variants;
- incidents created per day and digest items per day (now meaningful, unlike candidate-level routes);
- time from incident start to creation; recovery represented (`RECOVERING` within a tolerance of true recovery);
  no system transition to a terminal state;
- impact: median relative error and interval coverage vs `true_impact`, at incident level;
- **route source for evaluation** while Jev is blocked: grouping and lifecycle are evaluated on all decided
  candidates (route-agnostic) and with `baseline_v2` routes (labelled as the control); the fake client is used only
  in technical tests.

## 0.11. Scope boundary

Out: investigation agent and Mode B (Stage 7), API and UI (Stage 8), notifications or paging integrations, generated
explanations, Jev live calls, HELDOUT, new dependencies without an ADR, any write to billing state.

---

# Phase 1 — Implementation (after Gate 0 approval)

`src/pulseos/incidents/` (correlation, incident object, lifecycle, digest items, impact aggregation, event
stream, storage); remove `incidents` from the stage guard in the same change. Tests: correlation (merge / no merge /
related, the campaign + outage negative case, merging two incidents), deterministic replay of an event stream,
lifecycle (every allowed and forbidden transition, hysteresis, no system or AI terminal transition, actor and reason
on every row), digest promotion, impact aggregation without double counting, append-only storage, ground-truth
isolation, end-to-end on a simulated world.

# Phase 2 — Evaluation on DEV seeds ⛔ Gate 1

The 0.10 protocol on 20 seeds × realism v1/v2; tuning on 1–10, validation on 11–20; per scenario kind; route-agnostic
and `baseline_v2`-routed runs reported separately.

# Phase 3 — Documentation and PR

`docs/INCIDENTS.md`, ADRs for Gate 0 / Gate 1, stage tables, PR `stage-6-incidents` → `main`, not merged.

---

# Acceptance

- [ ] Gate 0 design approved; decisions recorded as ADRs (correlation rules, lifecycle details, storage DDL).
- [ ] Correlation deterministic and auditable; the campaign + outage case does not merge (all variants); no Jev-created links.
- [ ] Lifecycle follows ADR-028; only humans reach terminal states; every transition logged with actor and reason.
- [ ] Recovery represented; no flapping close.
- [ ] Impact labelled `OBSERVED` / `ESTIMATED`, compared with `true_impact` in evaluation only.
- [ ] DEV report: incident recall, duplicates, purity, wrong merges, incidents and digests per day, impact error;
      tuning and validation separate; HELDOUT untouched.
- [ ] Harness and product tests green locally and in CI; PR open, not merged.
