# Task: Stage 3 — statistical candidate detection (spec 04)

Prepared: 2026-10-02 · Branch: `stage-3-detection` · Read first: `AGENTS.md` → `docs/specs/04_ANOMALY_DETECTION.md`
(with its amendments) → `docs/METRICS.md` → `docs/DECISIONS.md` (ADR-026, ADR-031, ADR-029, ADR-032) →
`docs/SIMULATION.md` §11–13 → `docs/INVARIANTS.md` (INV-015, INV-016).

Work in phases. **Stop and report at every ⛔.** Report format: what changed · decisions (ADR ids) · tests
(passed / failed / skipped + reason) · measurements · open questions.

## Purpose

Answer **"what changed?"** deterministically, on the metric series of Stage 2, and nothing more: no severity,
no cause, no incident. Output is a stream of `AnomalyCandidate`s that Stage 4 (cohorts) and Stage 5 (Jev)
consume. Detection never reads ground truth (INV-015, test-enforced); only the evaluation code does.

## Phase 0 — Design ⛔ Gate 0 (no code before approval)

Send a design note covering:

1. **Series and grains** (ADR-026): global 15 min, per PSP 1 h, daily for slow drift; metrics:
   `authorization_rate`, `checkout_conversion_rate`, `renewal_success_rate`, `refund_rate`,
   `duplicate_charge_rate`, `attempt_volume`. Which metric/scope pairs are monitored, and why.
2. **Baseline** that does not absorb drift: frozen or lagged reference window, conditioned on hour-of-week
   (and the month-end dip); how many reference days; what happens in the first days of a world (warm-up).
3. **Detectors** (spec 04 list): absolute threshold; standardized deviation with an **overdispersion-aware**
   variance (not plain binomial: realism v2 has dispersion 1.5–2.0); minimum sample (30 attempts, ADR-026);
   persistence (k of n windows); a cumulative detector for slow drift (CUSUM or equivalent); recovery.
   For each: input, output, parameters, failure modes.
4. **Candidate object** (spec 04 fields): `anomaly_id` (stable across replay), metric + version, scope,
   window, observed, expected, delta, relative delta, score, sample size, method, `status ∈ {candidate,
   suppressed, recovered, promoted}` with explicit transition rules; evidence ids pointing at metric queries.
5. **Mode A prefilter** (deterministic, explainable): low-volume gate, duplicate-candidate suppression
   (same metric/scope/overlapping window), merge of adjacent windows into one candidate. Items of spec 04
   with no data source in the simulator (health checks, probes, maintenance) are listed as "not applicable yet".
6. **Replay driver, no future leakage**: detection runs at successive `as_of` times; it sees only events with
   `occurred_at < as_of` **and** `ingested_at <= as_of` (late events from `data_pipeline_issue` must be
   invisible until delivered). Say what this needs from the metrics layer (an `as_of` / ingestion bound).
7. **Evaluation rules** (the part most open to self-deception — decide before seeing results):
   - matching a candidate to a ground-truth record (time overlap with `[start, end]`, scope compatible with
     the affected cohort, metric family compatible with `affected_metrics`);
   - **recall** over incidents with `oracle_detectable_at != null` only (ADR-031 owner decision);
   - **latency** from `max(start, oracle_detectable_at)` to the first matching candidate;
   - **false positives**: candidates matching no record, per world-day; candidates matching `suppress`
     records reported separately (seasonality, benign shocks, campaign, tiny cohort, mix-shift promotion);
   - `watch` records: reported, not counted in recall or precision;
   - `unchanged_metrics`: a candidate on a metric the record declares unchanged counts as a false positive.
8. **Seeds and tuning**: parameters are tuned on `DEV_SEEDS` only, randomized schedule, scale 1.0, both
   realism v1 and v2. `HELDOUT_SEEDS` are **not touched in Stage 3** (they are for Stage 9). Cost estimate
   (generation + load + normalize per seed) and where the runs execute (embedded ClickHouse or CI).

## Phase 1 — Detection layer

`src/anomalyos/detection/` (remove `detection` and `detector.py` from the stage guard in the same change):
baseline, detectors, prefilter, candidate model, replay driver. Pure functions over metric series where
possible; ClickHouse access only through `anomalyos.metrics.compute` (INV-003). Unit tests on hand-built
series for every detector: step, ramp, recovery, seasonality, small sample, zero denominator, late data,
determinism. Ground-truth isolation test extended to `detection`.

## Phase 2 — Evaluation on DEV seeds ⛔ Gate 1

`src/anomalyos/evaluation/detection.py` (the only new code allowed to read ground truth) and a script that
runs the replay on DEV seeds and writes a report (not committed: `reports/` is generated output).
Report per scenario kind and overall, v1 vs v2: recall (oracle-detectable only), latency (median, p90),
false positives per day, suppress-record hits, misses with a reason, and the parameter values used.
A simple static-threshold baseline is reported next to the main detector (spec 10: the benchmark must be
able to show the simple method is as good).

⛔ **Gate 1:** report + proposed parameters. No re-tuning after this without a written reason.

## Phase 3 — Documentation and PR

`docs/DETECTION.md` (detectors, parameters, evaluation rules, results table), ADRs for the decisions of
Gate 0, stage table in `docs/ARCHITECTURE.md` and `docs/specs/STATUS.md`. PR `stage-3-detection` → `main`,
not merged.

## Out of scope

Cohort decomposition and multi-dimensional drill-down (Stage 4), Jev, incident engine, UI, held-out
evaluation, burst ranking with Jev (spec 04 defines only its interface here). No new dependencies
(no numpy/scipy: the statistics needed are small and stay in the standard library or in SQL).

## Acceptance

- [ ] Gate 0 design approved; decisions recorded as ADRs.
- [ ] Detectors, prefilter, candidate model, replay driver with unit tests; no ground-truth access (test).
- [ ] No future leakage: late events invisible before `ingested_at` (test with `data_pipeline_issue`).
- [ ] DEV-seed report: recall, latency, false positives, v1 vs v2, against a static-threshold baseline.
- [ ] Harness and product tests green locally and in CI; PR open, not merged.
