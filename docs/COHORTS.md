# Cohort intelligence (Stage 4)

Answers **"where is the change, and is it a rate change or a change of mix?"** for every promoted Stage 3 candidate —
no severity, no cause, no incident. Output: a `CohortAnalysis` and a bounded `EvidenceBundle` for Stage 5 (Jev).
Code: `src/pulseos/cohorts/`. Decisions: ADR-037 (design, Gate 0), ADR-038 (Gate 1 rules and results).
Ground truth is never read (INV-015); ClickHouse only through `metrics.compute` (INV-003).

## Inputs and queries

For a candidate (metric, scope, window, direction) the analysis pools a **before** period — the same window on days
d−8 … d−2, the most extreme day dropped when ≥ 4 remain (the Stage 3 baseline rule) — against the **during** window,
with first-look visibility. One `compute` call per approved combination whose dimensions the metric allows and which
adds at least one dimension to the scope:

| Kind | Combinations (`COMBINATIONS`, in this order) |
|---|---|
| single | psp, customer_country, payment_method_type, card_brand, platform, app_version |
| pairs | psp × customer_country, psp × card_brand, customer_country × payment_method_type |
| triple | psp × customer_country × platform |

Median cost on DEV: 9.5 queries and 0.27 s per candidate. Configuration is versioned (`COHORT_CONFIG_VERSION` = 3,
`CohortConfig` in `src/pulseos/cohorts/config.py`).

## Method

1. **Decomposition** (rates): the exact midpoint split of the total change into a rate part (cohort rates moved) and a
   composition part (traffic moved between cohorts), per combination; the composition part is centred on the mean
   rate. Reconciliation error is reported (≈ 1e−12). Counts: per-cohort excess events.
2. **Tests:** two-sample z on the arcsine scale (rates; Anscombe for counts), overdispersion φ from the reference
   days, gates of 30 attempts per period (rates) or 5 events (counts); Benjamini–Hochberg at q = 0.05 over all cohorts
   of the candidate; the number of cohorts examined is reported.
3. **New cohorts** (traffic now, no supported baseline — a new app version) are compared with the rest of the scope in
   the same window, as their own BH family. One that explains ≥ 60 % of the change is the locus, label `new_cohort`.
4. **Label:** the combination with the smallest rate share decides — ≥ 0.7 `rate_change`, ≤ 0.3 `mix_shift`, else
   `mixed`; `insufficient_data` without two supported cohorts. `attempt_volume` candidates whose locus approval moved
   by |z| < 2 are `volume_only`.
5. **Locus:** among discoveries that explain ≥ 60 % of the change, the most specific — but a sub-cohort replaces a
   parent (the scope, or a tested cohort on a subset of its dimensions) only if share of the parent's change /
   share of the parent's **baseline** traffic ≥ 1.25 (concentration rule). Ties: fewer attempts, fewer dimensions,
   earlier combination. Dimensions that do not change the population are dropped (canonical locus). With
   `mix_shift` the locus is the cohort pushing the total hardest through composition.
6. **Controls:** up to 3 cohorts differing from the locus in one dimension with |z| < 1.
7. **Impact** (`estimated`): lost successes (rates down) or excess events, with a 95 % interval scaled by √φ, for the
   candidate window; new cohorts use the rest of the scope as baseline.
8. **Bundle:** candidate, label, decomposition, locus, top-5 cohorts, controls, ≤ 2 related metrics, linked candidates,
   impact, counts, queries, flags; every item carries an epistemic label and an evidence id; ≤ 8 KB (max 3.9 KB on DEV).

The **daily cohort sweep** (S12, `authorization_rate` on psp × country and country × method) exists but is off in
Mode A (`sweep_enabled = False`, ADR-038): 0.09 / 0.29 false positives per day on v1 / v2 for +2 / +6 detections of
~330.

## Evaluation rules (fixed at Gate 0, additions at Gate 1)

- Per incident, the **first matching** Stage 3 candidate (Stage 3 matching rules) is scored.
- Localization against `root_cause.locus`: exact / over-specific / coarse / wrong (dimensions and values) — the
  headline (C-10). Beside it, **equivalence-aware** exact (ADR-038): synthetic-world equivalences
  (`app_version=web` ≡ `platform=web`, `card_brand=unknown` outside cards, card-only PSPs, `psp=unknown` for
  cancellations), `channel=renewal` implied by the renewal metrics, and `affected_cohorts` also accepted.
- Top-3 = the locus or one of the first three ranked cohorts; "any candidate exact" over all matching candidates.
- Labels: incidents expect `rate_change`; `fraud_like_spike` (injected traffic) `mix_shift` / `mixed`; app-version
  regressions `new_cohort` / `rate_change`; suppress records of mix kinds `mix_shift` / `volume_only`.
- Baseline: the naive locus (largest relative move, no decomposition, no multiple-testing control) on the same tables.
- Impact is a diagnostic only: the candidate window covers a median 75 % of the incident, while ground truth counts
  the whole incident; the accuracy check moves to Stage 6.

Code: `src/pulseos/evaluation/cohorts.py`; runner: `scripts/eval_cohorts_dev.py` (DEV seeds only; refuses HELDOUT
seeds; writes `reports/`, not committed).

## Results on DEV seeds (20 seeds × realism v1/v2, scale 1.0, randomized calendar)

| Run | Change | Top-1 exact v1 / v2 | Equivalence-aware | Wrong | Any candidate exact | Naive |
|---|---|---|---|---|---|---|
| 1 | design as approved (ADR-037) | 22 / 21 % | 37 / 36 % (diagnostic) | 34 / 36 % | 36 / 36 % | 1 / 2 % |
| 2 | ADR-038: canonical locus, concentration 1.5 (during shares), new cohorts, sweep off | 41 / 43 % | 58 / 59 % | 17 / 21 % | 48 / 48 % | 1 / 2 % |
| **3** | concentration 1.25 on baseline shares | **44 / 45 %** | **61 / 61 %** | 21 / 22 % | **55 / 56 %** | 1 / 2 % |

Run 3: over-specific 22 / 21 %, coarse 13 / 12 %; labels correct for 83 / 82 % of incidents. Exact or nearly so:
duplicate charges 20/20, refund spikes 10/10, PSP degradation 16/17, data pipeline 15/19, app-version regressions
15/20 (`new_cohort`), country degradation 11/15 (14 equivalence-aware), recoveries 11/14, dunning 17/18
equivalence-aware.

## Known limitations

| Limitation | Evidence (run 3, v1) | Where it is addressed |
|---|---|---|
| Locus keeps dimensions of the Stage 3 scope (sepa_debit found inside `psp_beta`) | payment-method degradation 19/20 over-specific | Not decided; candidate for a parent-level search |
| First candidate may belong to a neighbouring incident | simultaneous incidents 18/40 wrong; gradual drift 10/20 | Stage 6 (incident grouping); "any candidate exact" 55 % |
| Card testing: injected traffic is mostly composition | 1/19 exact, 9 coarse (`psp_alpha`), 9 over-specific (`… × web`, the injected cohort) | Ground truth names psp × country only; accepted for now |
| `channel` is not a cohort dimension | renewal failure 8/20 equivalence-aware | Renewal metrics imply it; checkout-side candidates do not |
| Labels on suppress (mix) records | 45 / 36 % | Mostly `attempt_volume` without `volume_only`; Stage 5 |
| Price-change label | 0/18 (`subscription_cancellation_rate`) | Not analysed yet |
| Impact accuracy | median rel. error 0.33, interval coverage 32 % (window vs whole incident) | Stage 6 |
