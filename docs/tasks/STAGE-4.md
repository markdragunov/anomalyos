# Task: Stage 4 — cohort intelligence and evidence narrowing (spec 05)

Prepared: 2026-10-03 · Branch: `stage-4-cohorts` (from `stage-3-detection`; rebase onto `main` once PR #7 is merged)
Read first: `AGENTS.md` → `docs/specs/05_COHORT_INTELLIGENCE.md` (with amendments) → `docs/DETECTION.md` →
`docs/METRICS.md` → `docs/DECISIONS.md` (ADR-026, ADR-033, ADR-034, ADR-035) → `docs/INVARIANTS.md` (INV-003, INV-013, INV-015).

Work in phases. **Stop and report at every ⛔.** Report format: what changed · decisions (ADR ids) · tests
(passed / failed / skipped + reason) · measurements · open questions.

## Purpose

Answer **"where is it happening, and is it a rate change or a mix change?"** for each promoted Stage 3 candidate,
and hand Stage 5 (Jev) a small, typed `EvidenceBundle` — the strongest decision-relevant evidence, not the most
evidence. Deterministic code only; no AI; ground truth is never read (INV-015).

Stage 3 left these gaps that Stage 4 is expected to close or explain (DEV run 3, main system):
mix-shift masking 9–11/19, short country × PSP outages and recoveries 13–15/20, and ~100 candidates from healthy
volume changes (holiday, campaign, promotion) that Stage 4 should label as composition, not rate, changes.

## Phase 0 — Design ⛔ Gate 0 (no code before approval)

A design note covering:

1. **Input / output contracts.** Input: a promoted `AnomalyCandidate` (+ world bounds). Output: `CohortAnalysis`
   (ranked cohorts, decomposition, controls, flags) and an `EvidenceBundle` with a hard size bound (top-k cohorts,
   controls, related metrics, impact), every item carrying an evidence id and an epistemic label
   (`observed` / `estimated`; INV-013).
2. **Approved dimensions and combinations as versioned configuration** (spec 05: PSP × country, PSP × card brand,
   country × payment method, PSP × country × platform; plus single dimensions), with cardinality limits and how a
   change to the list is reviewed (INV-003).
3. **Pooled comparison:** candidate window vs the Stage 3 baseline period (same lag rule, no future data), pooled
   per cohort; < 30 attempts → `insufficient_data`, never ranked (ADR-026). Queries only through `metrics.compute`
   with first-look visibility; how many queries per candidate (cost bound).
4. **Rate vs mix decomposition** of the parent change: ΔR = Σ w̄ᵢ·Δrᵢ (rate) + Σ r̄ᵢ·Δwᵢ (composition), midpoint
   weights so the parts sum exactly; reconciliation tolerance; how a candidate is labelled `rate_change`,
   `mix_shift` or `mixed`. Required tests: harmless seasonality and the campaign → mix; masked drop → rate in one
   cohort with ≈ 0 global change; Simpson's paradox fixture.
5. **Ranking and multiple testing:** score per cohort (contribution to the parent change, significance on the
   stabilized scale of Stage 3), a false-discovery control (e.g. Benjamini–Hochberg at a configured q) and the
   number of cohorts examined reported in every analysis.
6. **Controls:** how sibling cohorts that did *not* change are chosen and reported (they are the negative evidence
   Jev needs).
7. **Impact estimate** (`estimated`, never `observed`): lost successful payments and amount per currency =
   (expected − observed rate) × attempts × amount, with an interval; compared with ground-truth `true_impact` in
   evaluation only.
8. **Cohort sweep (open question):** masked drops produce no parent candidate at all. Options: (a) leave them to the
   benchmark as known misses; (b) a bounded daily sweep of approved two-dimensional cohorts under the same FDR
   control, producing extra candidates. Recommend one, with its false-positive budget.
9. **Grouping:** does Stage 4 group related candidates (nested scopes, overlapping windows) or is that Stage 6
   (incident correlation, spec 07)? Proposal and the boundary between the two.
10. **Evaluation rules, fixed before results:** localization top-1 / top-3 against ground-truth `affected_cohorts`
    (match rules per dimension); correct `rate_change` / `mix_shift` label on records that declare it (seasonality,
    campaign, promotion, masked drop); impact error vs `true_impact` (median relative error, coverage of the
    interval); bundle size; cost per candidate; effect of the sweep (if any) on Stage 3 recall and false positives.
    DEV seeds only, v1 and v2; HELDOUT untouched.

## Phase 1 — Implementation

`src/pulseos/cohorts/` (remove `cohorts` from the stage guard in the same change): configuration, pooled cohort
queries, decomposition, ranking with FDR, controls, impact, `EvidenceBundle`; optional sweep if approved.
Unit tests on hand-built cohort tables (decomposition sums exactly, Simpson's paradox, small support, FDR on null
cohorts, bundle bound, determinism, stable ids), an end-to-end test on a simulated world, ground-truth isolation
extended to `cohorts`.

## Phase 2 — Evaluation on DEV seeds ⛔ Gate 1

Extend `evaluation/` and `scripts/eval_detection_dev.py` (or a sibling script) with the Stage 4 rules; report
per scenario kind and overall, v1 vs v2, plus a simple baseline (rank cohorts by relative drop only, the method spec
05 says not to use) so the benefit of decomposition and FDR is measured, not assumed.

## Phase 3 — Documentation and PR

`docs/COHORTS.md`, ADRs for Gate 0 / Gate 1 decisions, stage tables, PR `stage-4-cohorts` → `main`, not merged.

## Out of scope

Jev, incident engine and correlation rules (Stage 6), UI, held-out evaluation, new dependencies.

## Acceptance

- [ ] Gate 0 design approved; decisions recorded as ADRs.
- [ ] Decomposition reconciles exactly; mix vs rate labelling tested on fixtures and simulator scenarios.
- [ ] FDR control in place; cohorts examined reported; bundle size bounded.
- [ ] Impact labelled `estimated`, compared with `true_impact` in evaluation only.
- [ ] DEV report vs the relative-drop baseline; v1 vs v2.
- [ ] Harness and product tests green locally and in CI; PR open, not merged.
