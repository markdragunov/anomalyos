# Design: duplicate incidents — options A, B, C (Gate 1)

Prepared: 2026-10-07 · Brief: `docs/tasks/SCOPE-DIMENSIONS.md` · Gate 0: ADR-051 · Status: **approved** by the owner
at Gate 1 (2026-10-07, thresholds as proposed); recorded as ADR-052.

Inputs from Gate 0 (tuning seeds 1–10, v1 / v2, `G` = 6 h): 136 / 125 duplicates; different cohorts 34 / 38 % (23 / 29
of them a country-series candidate against an incident located on `{psp}`), no approved chain 26 / 22 %, metric
groups 15 / 16 % (renewal 9 / 8), time 7 / 10 %, strict scope 7 / 3 %.

## D-0. What changes and what does not

- **Changes:** Stage 6 correlation (`incidents.correlate`, `incidents.config`), the Stage 6 pipeline (`incidents.pipeline`
  builds an extra locus for option A) and one new pure helper in `cohorts` for that locus.
- **Unchanged:** the Stage 4 locus, label, bundle and evidence ids; Stage 5 states and decisions; the Stage 7 loop;
  the simulator (golden digests stay); the card-testing split and `G` (ADR-042). Jev creates no relationship.
- Each option is a switch in `IncidentConfig`, so one pipeline run per world can be refolded under every variant.
  The chosen combination becomes the default, version `incidents_v4`.

## D-1. Option A — a parent locus for correlation

**Problem.** A PSP outage also moves approval in the countries where that PSP carries traffic. The country-series
candidate's Stage 4 locus always keeps the country (`{DE}`, `{DE, sepa_debit}`), so it can never nest with an
incident located on `{psp_beta}`.

**Rule.** For a candidate with a non-global Stage 3 scope `S` (first look and every checkpoint):

1. Run the Stage 4 analysis on the same metric, window and direction with a **global** scope (the "parent analysis";
   same pooled periods, same `CohortConfig`).
2. Its locus `P` is the candidate's **parent locus** if all hold:
   - `P` is a sub-cohort (not global, not a new cohort) and shares no dimension with `S`;
   - the cohort `S ∪ P`, measured on the same pooled periods, moves in the candidate's direction with |z| ≥ 2 (the
     Stage 7 `moved` threshold): the scope's own change is consistent with `P`.
3. Otherwise there is no parent locus.

**Use.** Only correlation uses it: a candidate nests with a member if its locus or its parent locus nests with the
member's locus or parent locus. The Stage 4 locus, the incident's `affected_dimensions`, impact and Stage 7 are
unchanged. The parent locus is stored on `CandidateInfo` and in the link evidence (`"cohort": "parent"`), so every
join it causes is explainable.

**Cost.** One extra Stage 4 analysis per non-global candidate and checkpoint, plus one pooled query for `S ∪ P`.
Measured in seconds per world.

## D-2. Option B — nesting beyond the fixed chains

Two variants, chosen on tuning seeds (D-5):

- **B-pairs:** two specific loci nest when one's (dimension, value) pairs contain the other's, whatever the
  dimensions. Conflicting values never nest.
- **B-chains:** keep the chain table and add the edges the diagnosis found at least twice:
  `app_version ⊂ platform × app_version`, `psp ⊂ psp × platform`, `psp × platform ⊂ psp × country × platform`,
  `customer_country ⊂ customer_country × platform`, `psp × country ⊂ psp × country × card_brand`. The transitive
  closure applies as today.

The global-locus rules (ADR-041 amendment) and the refinement rule (ADR-042) stay. They use the same `nested` function,
so they follow the chosen variant.

## D-3. Option C — renewal and dunning drops with approval drops

`authorization_rate` **down** joins the `subscriptions` group in addition to `payments`. A renewal or dunning drop
and an approval drop then share a group; time and cohort nesting still have to hold, e.g. renewal `{psp_beta}` with
approval `{psp_beta}` or `{psp_beta, DE}`. `authorization_rate` up, conversion and volume are unchanged. Risk: a PSP
degradation and an unrelated renewal-job failure on the same PSP at the same time merge. The wrong-merge check
(unrelated pairs in ground truth) measures it. This amends ADR-041 D-2.

## D-4. Determinism, audit, invariants

The rules are pure functions of the event stream and the config version (INV-006); the parent analysis goes through
`metrics.compute` like Stage 4 (INV-002, INV-003). Link evidence records which rule joined (`nested`, `parent`,
`pairs`, `group: subscriptions`). No ground truth outside evaluation (INV-015), no clock, no new dependency or
package.

## D-5. Evaluation protocol — fixed before results

Script `scripts/eval_duplicates_dev.py` (refuses HELDOUT): per world, one pipeline run with parent loci computed, then
engine refolds for each variant. Route-agnostic events (the Stage 6 headline), `G` = 6 h, `H` = 0.

1. **Tuning seeds 1–10 (v1 + v2 pooled):** baseline (`incidents_v3`), A, B-pairs, B-chains, C — each alone.
2. **Acceptance of an option:**
   - duplicates per covered record below the baseline;
   - wrong-merge rate at most the baseline + 1 point;
   - campaign + outage merges 0;
   - coverage at least the baseline − 1 point.
3. **B variant:** the passing one with fewer duplicates; within 0.02, B-chains (narrower).
4. **Combination:** all passing options together. If it fails acceptance, drop the option with the smallest
   duplicate gain and retry.
5. **Validation seeds 11–20** (seen for Stages 6–7, so not a clean validation; HELDOUT untouched) for the chosen
   combination against the baseline:
   - Stage 6: duplicates by reason (the Gate 0 classifier), coverage, purity, wrong and campaign merges, incidents
     per day;
   - Stage 7 control: top-1 / top-3, renewal failures, false-positive incidents (`scripts/eval_investigation_dev.py`);
   - option A only: parent-locus accuracy against the root cause, and cost.

## D-6. Tests (Phase 2)

Unit tests for:
- every new nesting edge and the B-pairs rule, including that conflicting values never nest;
- the parent-locus conditions (shared dimension, global or new cohort, weak `S ∪ P`);
- C grouping in both directions, and that `authorization_rate` up does not join subscriptions;
- campaign + outage pairs from ADR-042 still not merging under every variant;
- the defaults equal the chosen combination.

## D-7. Scope

No change to Stage 4 metrics, Stage 5, Stage 7 rules, the simulator or `G`. Burst ranking, human merge and split
commands, and a cross-incident view for Stage 7 stay out.
