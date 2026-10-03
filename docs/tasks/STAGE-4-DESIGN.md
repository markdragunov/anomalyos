# Stage 4 — design note (Gate 0)

Status: **proposed**, awaiting owner approval. No cohort code exists yet. Decisions marked **C-n** become ADRs.
Sizes below are measured on Stage 3 run 3 (DEV seeds): 40–67 promoted candidates per world (median 52).

## C-1. Contracts

**Input:** one promoted `AnomalyCandidate` (Stage 3) and the world bounds.
**Output:** `CohortAnalysis` (internal, complete) and an `EvidenceBundle` (handed to Stage 5, bounded):

| Bundle field | Content | Epistemic label | Bound |
|---|---|---|---|
| `bundle_id`, `candidate` | id, metric, scope, window, observed / expected | observed | 1 |
| `label` | `rate_change` / `mix_shift` / `mixed` / `volume_only` / `insufficient_data` | inferred | 1 |
| `decomposition` | rate effect, composition effect, total, reconciliation error | observed (arithmetics on observed counts) | 1 |
| `locus` | the cohort judged to carry the change (C-5) | inferred | 1 |
| `top_cohorts` | cohort, attempts before / during, rate before / during, contribution, z, q-value, evidence id | observed (+ q inferred) | ≤ 5 |
| `controls` | sibling cohorts that did not move (C-6) | observed | ≤ 3 |
| `related_metrics` | the locus on 2 sibling metrics (e.g. approval → conversion, technical-failure share) | observed | ≤ 2 |
| `impact` | lost successful payments and amount per currency, interval | **estimated** | 1 |
| `cohorts_examined`, `queries`, `flags` | multiple-testing denominator, cost, reasons | observed | — |

Serialized bundle ≤ 8 KB (tested). Every item carries an evidence id (stable across replay).

## C-2. Dimensions and combinations (versioned configuration, INV-003)

`cohorts/config.py`, `COHORT_CONFIG_VERSION = 1`: singles `psp`, `customer_country`, `payment_method_type`,
`card_brand`, `platform`, `app_version`; pairs `psp × customer_country`, `psp × card_brand`,
`customer_country × payment_method_type`; triple `psp × customer_country × platform`. Cardinality cap 200 cohorts
per combination; a combination is used only if the candidate metric allows its dimensions (metric registry).
Changes go through a reviewed edit of this file and bump the version (recorded in every analysis).

## C-3. Pooled comparison

For each combination one query: `metrics.compute(metric, start = window_start − 8 d, end = window_end, grain = 1 h,
group_by = combination, filters = candidate scope, visibility = "window_close")`. In Python (compact rows only):
**during** = the candidate's windows; **before** = the same hours of days d−8 … d−2 (Stage 3 baseline rule), pooled.
Cohorts with < 30 attempts in either period → `insufficient_data`, not ranked (ADR-026).
Cost: ≈ 8 queries per candidate (singles and combinations allowed for the metric) → ≈ 400 per world.

## C-4. Rate vs composition decomposition (rate metrics)

R = Σ wᵢ·rᵢ (wᵢ = share of attempts). Midpoint decomposition, exact by construction:
ΔR = Σ w̄ᵢ·Δrᵢ (**rate effect**) + Σ r̄ᵢ·Δwᵢ (**composition effect**), w̄, r̄ = mean of before / during.
A cohort absent in one period gets that period's rate from the other (no rate effect). Reconciliation error is
reported (tested ≈ 0). Label on the combination that explains the parent best:
rate share = |rate| / (|rate| + |composition|) ≥ 0.7 → `rate_change`; ≤ 0.3 → `mix_shift`; else `mixed`.
**Count metrics** (volume, refunds): contributions are Δcount per cohort; label `volume_only` if the locus's
`authorization_rate` did not move (|z| < 2), else `rate_change` — this is what lets Stage 5 recognise healthy
demand (holiday, campaign, promotion) without Stage 4 suppressing anything.

## C-5. Significance, multiple testing, locus

Per cohort: z on the Stage 3 stabilized scale (arcsine / Anscombe) between during and before, with φ estimated per
combination from the baseline hours (robust, floored at 1). One-sided p in the parent's direction.
**Benjamini–Hochberg** across *all* cohorts examined for the candidate, q = 0.05; only discoveries are ranked.
Ranking: contribution to the parent's rate effect (count metrics: to Δcount).
**Locus** = among discoveries whose rate effect covers ≥ 60 % of the parent's rate effect, the one with the fewest
attempts (most specific); if none reaches 60 %, the top contributor; if there are no discoveries, `locus = null`.

## C-6. Controls

Up to 3 cohorts of the locus's combination that differ from the locus in exactly one dimension value, have ≥ 30
attempts in both periods and |z| < 1 (they did not move), ordered by attempts. No sibling qualifies → flag
`no_control`.

## C-7. Impact (`estimated`)

Rate metrics: lost successes = (r_before − r_during) × n_during for the locus (interval from the binomial variance
of the difference, ×√φ); amount = lost × average captured amount per currency of the locus in the before period
(`revenue_collected_minor` / successes). Count metrics: excess count = during − expected. Never labelled observed.

## C-8. Cohort sweep — recommendation (b)

Masked drops produce no parent candidate (global and per-PSP series stay flat). Recommend a **bounded daily sweep**:
for every complete day after warm-up, `authorization_rate` on `psp × customer_country` and
`customer_country × payment_method_type` (≈ 30 cohorts with ≥ 30 attempts per day), before = days d−8 … d−2,
BH at **q = 0.01** across the day's cohorts, discoveries become candidates `S12` (`method = cohort_sweep`) and go
through the same Stage 4 analysis. Expected false-positive budget ≤ 0.05 per day (BH at q = 0.01 on ~30 tests);
the sweep is dropped if DEV shows more than 0.1 false positives per day from it. Latency is a day by design.
Alternative (a): no sweep; masked drops stay a documented miss.

## C-9. Grouping boundary

Stage 4 does **not** merge candidates. It links them: the bundle lists `related_candidates` (same metric family,
overlapping windows, nested loci). Merging into incidents is Stage 6 (spec 07, ADR-028 rule).

## C-10. Evaluation rules (fixed before results)

Computed on candidates that Stage 3 scoring matched to an `incident` record (true positives), DEV seeds, v1 and v2:
- **Localization** vs the record's `root_cause.locus`: `exact` (same dimensions and values), `over_specific`
  (contains the truth and adds dimensions), `coarse` (contained in the truth), `wrong`; top-1 = locus exact;
  top-3 = an exact cohort among `top_cohorts`.
- **Label** vs expectation: records of `harmless_seasonality`, campaign (`correlated_unrelated_anomalies` suppress
  record), promotion (`mix_shift_masking` suppress record) → `mix_shift` or `volume_only`; every other incident on a
  rate metric → `rate_change`. Reported as accuracy per kind.
- **Impact:** for approval / conversion incidents, estimated lost payments vs `expected_impact.lost_successful_payments`:
  median absolute relative error and interval coverage.
- **Bundle:** size distribution, max ≤ 8 KB; cost: queries and seconds per candidate.
- **Sweep:** Stage 3 recall and false positives per day with and without S12.
- **Baseline:** rank cohorts by relative drop only (no decomposition, no FDR) → same localization metrics.

## Open questions for the owner

1. Sweep: (b) as above (recommended) or (a) no sweep?
2. Locus rule thresholds (60 % coverage, rate-share 0.7 / 0.3, BH q = 0.05 per candidate / 0.01 per sweep day):
   accept as fixed-before-results values?
3. Count metrics: `volume_only` labelling via the locus's approval (C-4) — accept?
