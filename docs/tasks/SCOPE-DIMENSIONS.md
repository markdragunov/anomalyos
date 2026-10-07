# Task: Scope dimensions in loci — fewer duplicate incidents (deferred from Stage 4 / Stage 6)

Prepared: 2026-10-07  
Status: **Approved** by the owner (2026-10-07; option C stays an option for Gate 1). Decisions start at ADR-051  
Branch: `scope-dimensions` (from `main` after PR #14)

## Read first

`AGENTS.md` → `docs/COHORTS.md` (known limitations) → `docs/INCIDENTS.md` (correlation, known limitations) →
`docs/INVESTIGATION.md` (known limitations) → `docs/DETECTION.md` (series and scopes) → `docs/DECISIONS.md`
(ADR-037, ADR-038, ADR-041 with its amendment, ADR-042, ADR-050) → `docs/INVARIANTS.md` (INV-006, INV-015, INV-016).

Work in phases. Stop and report at every ⛔ Gate. Every report: what changed · decisions (ADR ids) · tests (passed /
failed / skipped, with reasons) · measurements · open questions · whether the next phase is authorized.

---

## Purpose

Stage 4 searches for the locus **inside** the Stage 3 scope, so every locus keeps the scope's dimensions: a
`sepa_debit` failure detected on the `psp_beta` series gets the locus `psp_beta × sepa_debit`, not `sepa_debit`. Stage 6
nests loci along fixed dimension chains, so loci that carry different scope dimensions for the same event may not
nest, and one event becomes several incidents. This task measures **why** duplicates happen, then fixes the causes
that the measurement supports — in Stage 4 (locus), Stage 6 (nesting) or both — without new wrong merges.

## What is known (hypotheses to verify — numbers come from the docs, not re-measured yet)

- **Duplicates:** 0.86 / 0.82 engine incidents per covered record beyond the first (validation, v1 / v2), 0.99 / 0.98
  on tuning seeds (`docs/INCIDENTS.md`). Not yet broken down by reason.
- **Over-specific loci:** payment-method degradation 19/20 over-specific because of the inherited `psp` (`docs/COHORTS.md`,
  run 3); over-specific overall 22 / 21 %.
- **Renewal failures** (Stage 7 top-1 10 %): the renewal-metric candidate forms its own incident mostly because of the
  **metric groups** — `renewal_success_rate` is in "subscriptions", `authorization_rate` in "payments"
  (`incidents/config.py`, ADR-041 D-2, ADR-042) — and because `channel` is not a cohort dimension. **This is not a
  scope-dimension effect;** fixing it would change the correlation rules (ADR-041 / ADR-042) and needs a separate owner
  decision (Phase 1, option C).
- Campaign + outage merges are 0 after ADR-042 and must stay 0.

---

# Phase 0 — Diagnosis ⛔ Gate 0

Measure before designing. Evaluation code only (it may read ground truth, INV-015); no product change.

1. On tuning seeds 1–10 (v1 / v2), classify **every duplicate** (an engine incident beyond the first that covers the
   same record) by the first rule that kept it apart from the record's main incident:
   - **scope:** the loci would nest if the Stage 3 scope dimensions were dropped;
   - **chain:** both loci are specific but no approved chain links their dimension sets;
   - **group:** the metric groups differ (e.g. subscriptions vs payments);
   - **time:** the gap exceeded `G`;
   - **global / refinement:** the ADR-041 amendment or the ADR-042 refinement rule;
   - **other**.
2. The same breakdown per scenario kind, and per cause for over-specific Stage 4 loci (which inherited dimension).
3. For renewal-failure records: how many candidates sit in a separate incident, and by which reason.
4. Report the table and a recommendation for Phase 1 (which options to build, expected effect).

Fixed now: duplicates are counted as in `evaluation/incidents.py` (Stage 6 rule); the classification reads only
engine state and candidate fields; the script refuses HELDOUT.

---

# Phase 1 — Design ⛔ Gate 1 (after Gate 0)

Options, chosen by the Phase 0 numbers:

- **A. Parent-level locus search (Stage 4).** Also test cohorts that drop a scope dimension (e.g. `sepa_debit` across
  all PSPs) on the same pooled periods; prefer the parent when it explains the change at least as well (a
  concentration rule as ADR-038). Cost: more pooled queries per candidate — measure.
- **B. Scope-aware nesting (Stage 6).** Compare loci after removing dimensions that come only from the Stage 3 scope,
  or add chains through the scope (e.g. `psp ⊂ psp × payment_method_type`). Each new chain needs a wrong-merge check.
- **C. Renewal grouping (Stage 6, only with an owner decision).** Let a subscriptions candidate join a payments
  incident on the same PSP within `G` when the approval drop is on non-checkout traffic — or keep the split and record
  it. Changes ADR-041 D-2 / ADR-042.

Gate 1 fixes: the option set, new config versions (`COHORT_CONFIG_VERSION`, the incident config version), and the
evaluation rules below.

---

# Phase 2 — Implementation

Smallest change for the chosen options, with unit tests per rule (nesting, parent search, no new merge path for
campaign + outage). No new dependency, no new package, no simulator change (golden digests stay).

---

# Phase 3 — Evaluation on DEV seeds ⛔ Gate 2

Rerun Stage 4, Stage 6 and Stage 7 DEV evaluations (tuning 1–10 to choose, then validation 11–20; seeds 11–20 are
already seen for Stages 6–7, so not a clean validation; HELDOUT untouched). Before / after:

- **Stage 4:** top-1 exact and equivalence-aware locus, over-specific / coarse, per cause.
- **Stage 6:** duplicates per covered record (by reason), coverage, purity, wrong merges, **campaign + outage merges
  = 0**, incidents per day.
- **Stage 7:** control top-1 / top-3, renewal failures, false-positive incidents.

Acceptance of a change: duplicates down, wrong merges not up by more than 1 point, campaign + outage merges 0.

---

# Phase 4 — Documentation and PR

`docs/COHORTS.md`, `docs/INCIDENTS.md`, `docs/INVESTIGATION.md` (results and limitations; fix the Stage 7 limitation
row that names this task as the renewal fix), ADRs, PR `scope-dimensions` → `main`, not merged.

---

# Acceptance

- [ ] Gate 0 diagnosis: duplicates broken down by reason; renewal split measured.
- [ ] Gate 1 options approved; decisions recorded as ADRs.
- [ ] Duplicates reduced without new campaign + outage merges; wrong merges within 1 point.
- [ ] Stage 4, 6 and 7 numbers reported before / after; HELDOUT untouched.
- [ ] Harness and product tests green locally and in CI; PR open, not merged.
