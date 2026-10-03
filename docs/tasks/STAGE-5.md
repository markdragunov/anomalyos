# Task: Stage 5 — Jev decision layer, verifier and policy, Mode A (spec 06)

Prepared: 2026-10-03 · **Draft for owner review** · Branch: `stage-5-jev` (from `main` after PR #9)
Read first: `AGENTS.md` → `docs/specs/06_JEV_INTELLIGENCE.md` (with amendments) → `docs/COHORTS.md` →
`docs/DETECTION.md` → `docs/DATA_MODEL.md` (JevState, cause vocabulary, severity, routes, semantic encoding) →
`docs/ARCHITECTURE.md` (§ AI boundaries, § 6 Jev layer, § 7 policy) → `docs/DECISIONS.md` (ADR-018, ADR-019,
ADR-024, ADR-027, ADR-028, ADR-037, ADR-038, OQ-1, OQ-5) → `docs/INVARIANTS.md` (INV-004, INV-005, INV-006,
INV-009, INV-010, INV-013, INV-015, INV-016).

Work in phases. **Stop and report at every ⛔.** Report format: what changed · decisions (ADR ids) · tests
(passed / failed / skipped + reason) · measurements · open questions.

## Purpose

Turn each promoted Stage 3 candidate with its Stage 4 analysis into a route — **`IGNORE` / `DIGEST` / `INCIDENT`** —
with an auditable record: code builds a small typed `JevState`, Jev answers a versioned question set, a verifier
checks the answers, deterministic policy decides. **Jev decides. Code verifies and routes.** No text generation, no
tools, no incident lifecycle.

What Stage 5 inherits (DEV, randomized calendar):
- Stage 3: recall 0.89 / 0.87, **0.53 false positives per day** (v1: 266 on 20 worlds — 177 match no record,
  52 match `suppress` records such as seasonality, campaigns and mix-shift masking, 37 hit a declared unchanged
  metric). These should end as `IGNORE` or `DIGEST`, not `INCIDENT`.
- Stage 4: top-1 exact locus 44–45 % (61 % equivalence-aware), labels correct for 82–83 % of incidents; known gaps in
  `docs/COHORTS.md` (scope dimensions in the locus, attribution between simultaneous incidents, impact accuracy).
  Jev must be judged with these inputs, not with ideal ones.

## Before Gate 0 — access (owner)

Jev is TypeSafe's hosted System One model (ADR-018, ADR-024). The repository has no endpoint, key, SDK choice or
pinned model version (OQ-1 open). The design can proceed without access; **evaluating Jev cannot**. Needed from the
owner: API access and documentation, the model version to pin, rate limits and price, and a budget per DEV run.
The key lives only in `.env` (never in git, never read by the coding agent); live calls are run with owner approval.

## Phase 0 — Design ⛔ Gate 0 (no code before approval)

A design note covering:

1. **Inputs and the `JevState`.** Input: candidate + `CohortAnalysis` + `EvidenceBundle` (+ linked candidates).
   State fields as named semantic values and **versioned buckets** (relative change, statistical strength, onset,
   share explained by the locus, rate vs mix label, impact size, recovery, related-metric agreement, calendar context),
   a hard size budget, and no free text, no raw timestamps, no `run_id` / `scenario_id`, no ground truth. How evidence
   ids are carried: in the state, or in a side map keyed by state field (proposal and why).
2. **Question set v1 (OQ-5).** Mode A triage: `is_incident` (noul), `severity` (choice `low…critical`), `category`
   (choice over the closed cause vocabulary v1), `needs_human` (noul), `recovery` (noul), optionally per-cause
   plausibility nouls and `novelty` (score). One judgment per question, wording rules, which question gates what.
   Designed against: a clear incident (PSP degradation), a slow drift, noise (small cohort, benign shock), a recovery,
   a healthy mix change (seasonality, campaign). Spec 06 says to measure which questions improve decisions: list the
   ablations.
3. **`JevClient` port and transport (ADR-024).** Typed request (question-set version, state, pinned model) → typed
   answers (probabilities, confidences, returned model version). Transport adapter in its own module, stdlib HTTP
   unless an SDK is justified (a new dependency needs an ADR); timeout; **no retry loop**; structured `model_error`.
   Replay store: raw response + question-set version + returned model version + state hash; modes `replay`
   (default in tests, CI and evaluation), `record` (live, owner-approved), `fake` (unit tests). Settings through
   `load_settings`.
4. **Verifier.** Schema, question ids, option membership, numeric ranges, probability consistency (per primitive;
   no identities across questions, ADR-018), required evidence ids, state hash, model / version metadata,
   cardinality, freshness. Never invents missing evidence; any failure → unverified.
5. **Policy.** Deterministic, versioned configuration: thresholds per question and per risk level; `unverified` or
   model error → `DIGEST` (ADR-028); how severity and `needs_human` enter the route. Plus a **no-Jev baseline
   policy** over the Stage 3/4 outputs alone (strength, label, locus share, impact bucket): it is the deterministic
   fallback (ARCHITECTURE § AI boundaries, "replaceable") and the control Jev must beat.
6. **Assessment and audit.** `Assessment` assembled in code from typed answers; a decision record per candidate with
   the spec 06 fields (decision id, mode, question-set version, state hash, requested and returned model version,
   raw answers, verifier status, policy version, route, evidence ids, timestamps as inputs). Storage: ClickHouse
   append-only table (as ADR-028 for incident state) or files until Stage 6 — proposal; a schema change needs an ADR.
7. **Evaluation rules, fixed before results.** Candidate ↔ record matching from Stage 3. Route accuracy against
   `expected_route` (`INCIDENT` = incident, `DIGEST` = watch, `IGNORE` = suppress); incident recall at the
   `INCIDENT` route; pages per day on unmatched and `suppress` candidates; digest volume; severity against
   ground-truth severity; category top-1 / top-3 against `true_cause`; calibration of `is_incident` (reliability,
   Brier); per question set ablation; cost (calls, latency, price) and failure rate. Jev vs the no-Jev baseline on
   the same candidates. DEV seeds only, v1 and v2; thresholds tuned on DEV; HELDOUT untouched.
8. **Cost bound.** About 50 candidates per world, so about 2,000 calls for 40 DEV worlds: record once, replay
   afterwards; budget and rate limits from the owner's answer.
9. **Scope boundary.** Proposal: Mode A only. Mode B drilldown questions (importance noul, choice over a shortlist)
   belong to the investigation loop (Stage 7, ADR-019 option 3); grouping candidates into incidents is Stage 6.

## Phase 1 — Implementation

Packages `src/anomalyos/jev/` (state builder, buckets, question set, client port, transport, replay, verifier,
assessment) and `src/anomalyos/policy/` (rules, routes, baseline policy, decision records). Remove `jev` and `policy`
from the stage guard in the same change. Tests: bucket edges and state size; no free text or ground truth in a state;
verifier rejects each malformed answer class; policy determinism (same inputs and version ⇒ same route); model error,
timeout and unverifiable answers ⇒ `DIGEST` with an audit record and no retry; replay reproduces a recorded run;
architecture tests that only the transport module does network I/O (INV-004 as clarified in ADR-024) and that
`jev` / `policy` never read ground truth (INV-015). CI never calls the live endpoint.

## Phase 2 — Evaluation on DEV seeds ⛔ Gate 1

Record Jev answers on DEV (owner-approved live run), then replay: route accuracy, pages per day, recall at
`INCIDENT`, severity and category accuracy, calibration, question ablations — Jev vs the no-Jev baseline, v1 vs v2,
per scenario kind. Thresholds tuned on DEV only. **Without access:** report the baseline policy and the full
pipeline on the fake client, and state plainly that Jev itself is not evaluated.

## Phase 3 — Documentation and PR

`docs/DECISIONING.md` (state, question set, verifier, policy, audit, results), ADRs for Gate 0 / Gate 1 decisions,
stage tables, PR `stage-5-jev` → `main`, not merged.

## Out of scope

Incident engine and grouping (Stage 6), investigation agent and Mode B loop (Stage 7), generative explanations
(ADR-027), UI, held-out evaluation, new dependencies without an ADR, any write to billing state.

## Acceptance

- [ ] Access answer recorded; Gate 0 design approved; decisions recorded as ADRs (incl. question set v1 = OQ-5).
- [ ] `JevState` bounded, bucketed, free of free text and ground truth; size reported.
- [ ] Verifier and policy deterministic and versioned; failures route to `DIGEST` and are audited; no retry loop.
- [ ] Replay is the default in tests, CI and evaluation; CI never calls the live endpoint.
- [ ] DEV report: Jev vs no-Jev baseline (or baseline only, stated, if no access); v1 vs v2.
- [ ] Harness and product tests green locally and in CI; PR open, not merged.
