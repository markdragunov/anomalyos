# Stage 1 — Stripe-like Billing Simulation + Scenario Engine

> **Status: DONE (sim-1.0.0).** The original brief is superseded by `docs/SIMULATION.md` (module map, world, scenarios, ground truth) and `docs/DATA_MODEL.md`. Only the delta is kept here. See `STATUS.md`.

## Delivered

- Deterministic, seeded generator: ≈1.37M events per run, Stripe-shaped IDs and lifecycle, no credentials, no claim of Stripe API compatibility (`ADR-021`).
- **14 scenario kinds**, not 13: the original list plus `normal_variation` as an explicit negative control. Every injected incident has a machine-readable ground-truth record, separate from events (`<db>_truth`, ADR-017), with counterfactual impact.
- Reference digests (seed 42, preset `full`, scale 1.0): `run_id=run_133c4a3a198eb8c1`, `events_sha256=1ac6d592…ece2a22`, `truth_digest=79bfe1fd…`.

## Delta still to do (`docs/tasks/SIMULATOR-FIXES.md`, branch `sim-1.1`)

1. Randomise the scenario calendar per seed (start, duration, cohort, strength) and record the realised values in ground truth; define `DEV_SEEDS` / `HELDOUT_SEEDS`.
2. Remove the two cause "labels" in data: failure codes that occur only inside effects, and the `risk_score` boost on effect-caused failures.
3. `oracle_detectable_at` in ground truth, so detection latency is measured against statistical distinguishability, not scenario start.
4. A less clean baseline (overdispersion, benign non-incident swings) with `normal_variation` ground truth.
5. Coverage: causes not yet exercised (`dunning_failure`, `pricing_or_plan_change`, `data_pipeline_issue`, `unknown`), simultaneous incidents, missing/contradictory evidence, late/out-of-order events, deployments, PSP status, incident history (data sources for the agent tools in spec 08).
6. Small ground-truth fixes (suppressed-record counterfactuals, `control_day` fields, secondary duplicate in `lost_payments`).