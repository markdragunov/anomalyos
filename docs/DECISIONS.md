# Decisions

Record important, durable choices here. Newest first. Coding memory lives in this file (and in `AGENTS.md` / architecture tests), not in a vector store.

Format: short ID, status, context, decision, consequences.

---

## ADR-040 — Stage 5 Gate 1: Jev evaluation blocked, JevState v2, baseline_v2 · *accepted* (2026-10-04, owner OK on recommendations 1–3, option A)

**Context.** DEV run on 40 worlds with the fake client (no Jev access, OQ-1): 2,116 first-look decisions, all audited,
failures routed to `DIGEST`. `baseline_v1` caught 17–18 % of incidents at `INCIDENT` on validation seeds; the binding
condition was impact — at first look the window is one series step, so 94 of 119 incident candidates (and 28 of 38
non-incidents) had `small` impact on edges meant for whole episodes. Stage 4 at first look: top-1 exact 41–42 %,
equivalence-aware 55–56 %, labels 57–58 % (full window: 44–45 / 61 / 82–83 %).

**Decision.**
1. **Gate 1 closes as "Jev evaluation: blocked — no live access"** (option A). The pipeline, audit, verifier, policy
   and the no-Jev control are built and measured; the live run, `policy_v1` threshold tuning and request-removal
   ablations wait for provider access and a budget. Stage 5 proceeds to documentation and PR.
2. **`JevState` v2:** `impact` is estimated lost successes (or excess events) **per hour** of the first-look window,
   edges 3.8 / 19 / 44 per hour = the 25 / 50 / 75 % quantiles over all decided candidates of DEV seeds 1–10 (no
   labels used). No Jev call has been made, so the schema change costs nothing.
3. **`baseline_v2`**, tuned on DEV seeds 1–10 only (`scripts/tune_stage5_dev.py`, rules fixed before results:
   maximum pooled incident recall at `INCIDENT` with (unmatched + suppress) → `INCIDENT` ≤ 0.10 per day; ties: fewer
   `INCIDENT` per day): `INCIDENT` if strength = strong, change type rate-like (`rate_change`, `new_cohort`, `mixed`)
   and impact provided; no impact-size or locus-share threshold won. Tuning seeds: recall 0.21 at 0.066
   non-incident `INCIDENT` routes per day. Validation on seeds 11–20 is reported in `docs/DECISIONING.md`.

**Consequences.** `STATE_SCHEMA_VERSION` = `jev_state_v2`, `BaselineConfig.version` = `baseline_v2`. The policy
safety rule (strong signal with medium or large impact is never ignored) now reads the per-hour buckets. HELDOUT and
validation seeds were not used for tuning.

---

## ADR-039 — Stage 5 design: first-look decisions, JevState v1, question set v1, policy v1 · *accepted* (2026-10-03, owner OK on Gate 0)

**Context.** Stage 5 turns promoted Stage 3 candidates with their Stage 4 analysis into `IGNORE` / `DIGEST` /
`INCIDENT` (brief `docs/tasks/STAGE-5.md`, design `docs/tasks/STAGE-5-DESIGN.md`). No Jev access exists yet (OQ-1
open): the pipeline runs on a fake client and replay; Jev itself is not evaluated until access exists.

**Decision** (design D-0 … D-12, owner's answers 1–6).
- **D-0 First look.** Mode A decides at `as_of = detected_at`. Stage 3 candidates gain two additive fields,
  `score_at_detection` and `methods_at_detection` (what and when Stage 3 detects is unchanged); Stage 4 runs on the
  as-of window `[window_start, detected_at)`; linked candidates are those detected by `as_of`. Episode-level fields
  (`score`, `window_end`, `method`, recovery, `parent_id`) never enter a decision. Observed recovery is not a v1 field.
- **D-1 `JevState` v1:** 18 closed-enum fields with versioned bucket edges, no dimension values, numbers,
  timestamps, ids or ground truth; hard budget 2,048 bytes of canonical JSON, rejected (never truncated) above it;
  evidence provenance in a code-owned side map that Jev never sees.
- **D-1 clarification (owner OK after Phase 1):** rises of rates (duplicates, fraud, cancellations, late data) use
  the rise scale `slight … extreme`, like counts; drops use `none … collapse`.
- **D-2** explicit `DecisionContext`; canonical JSON; `state_hash = sha256(schema_version + "\n" + json)`.
- **D-3 Question set v1 (resolves OQ-5):** `is_incident` (noul), `severity` (choice low…critical), `category`
  (choice, cause vocabulary v1, audit only), `needs_human` (noul; watch records as a stated weak proxy target).
  `recovery_likelihood`, per-cause plausibility and novelty deferred. Answer-removal ablations offline;
  request-removal ablations need live access.
- **D-4/D-5** `JevClient` port with fake, replay and HTTP implementations; the HTTP transport is the only module that
  may do network I/O and returns `not_configured` until the provider's wire format is known; no retry; replay
  artifacts are gitignored files keyed by request hash; a replay miss is an error, never a silent fake. The fake
  answers deterministically from the request hash and injects malformed / failing responses; it is not a model.
- **D-6** pure verifier with structured reason codes; nothing repaired or inferred.
- **D-7 `policy_v1`:** the 9-row decision table of the design (failure → `DIGEST`; safety: a strong signal with
  medium or large impact is never `IGNORE`d; healthy composition per Stage 4 cannot reach `INCIDENT` without
  P(severity ≥ high) ≥ 0.5; `category` never gates). `baseline_v1` (no Jev) is a benchmark control only.
  Thresholds fixed now, tuned only at Gate 1 on DEV seeds 1–10.
- **D-8** runtime audit in a ClickHouse append-only table `jev_decisions` (one row per decision, re-decisions are new
  rows; DDL in `anomalyos.policy.audit`), replay artifacts as files, evaluation joins only in `evaluation/`.
- **D-9** evaluation protocol of the design; status precedence incident > watch > suppress > unmatched; tuning seeds
  1–10, validation seeds 11–20 (validation clean only for Stage 5 thresholds).
- **D-10** budget guard before any client call; **D-11** calendar context `not_provided`; **D-12** Mode A only.

**Consequences.** Packages `jev` and `policy` are created and leave the stage guard. INV-004 holds for the decision
contract (ADR-024): network I/O only in `jev/transport.py`, tested. INV-015 holds: `jev` and `policy` never import the
simulator or evaluation. No new dependency.

---

## ADR-038 — Stage 4 Gate 1: locus rules, new cohorts, sweep report-only · *accepted* (2026-10-03, owner OK on recommendations 1–8)

**Context.** The first DEV evaluation (20 seeds × realism v1/v2, rules of ADR-037) localized only 22 % / 21 % of
incidents exactly with the first matching candidate (naive relative-drop baseline: 1–2 %). Diagnosis on DEV:
large sub-cohorts chosen over the degraded cohort (19 %), structurally equivalent dimensions (12 %), the first
candidate belonging to a neighbouring incident (12 %), metric-implied `channel` (renewal metrics), dimensions inherited
from the Stage 3 scope (6 %), new cohorts without a baseline (5 %). The sweep added 2 / 6 detections of ~330 at
0.094 / 0.286 false positives per day (threshold 0.1, fixed at Gate 0).

**Decision.**
1. The daily sweep (S12) is **off** in Mode A (`CohortConfig.sweep_enabled = False`); it runs only for reports.
2. **Canonical locus**: dimensions that do not change the population (identical before / during counts) are dropped;
   among equally specific cohorts the earlier combination in `COMBINATIONS` wins (platform before app_version).
3. **Concentration rule**: a sub-cohort replaces a parent (the scope, or a tested cohort on a subset of its dimensions)
   only if share of the parent's change / share of the parent's traffic ≥ `locus_lift` = 1.5.
4. **New cohorts** (traffic now, no supported baseline) are tested against the rest of the scope in the same window, as
   their own Benjamini–Hochberg family; one that explains ≥ 60 % of the change becomes the locus with label
   `new_cohort`; its impact uses the rest of the scope as baseline. `volume_only` applies to `attempt_volume` only.
5. Evaluation: the literal C-10 number stays the headline; an **equivalence-aware** number is reported beside it
   (synthetic-world equivalences, `channel=renewal` implied by renewal metrics, `affected_cohorts` also count).
   Top-3 includes the locus itself.
6. Impact accuracy and attribution between simultaneous incidents move to Stage 6 (incident grouping); Stage 4 keeps
   the interval labelled `estimated` and reports "any candidate exact" as the second number.
7. Expected labels: injected-traffic scenarios (`fraud_like_spike`) expect `mix_shift` or `mixed`; app-version
   regressions accept `new_cohort` (or `rate_change`).
8. The simulator is not changed (fraud and renewal `root_cause.locus` coarser than `affected_cohorts` is covered by 5).

**Follow-up (owner OK after the second DEV run).** With lift 1.5 on during-traffic shares, card testing lost its
locus (injected attempts inflate their cohort's share of during traffic) and a PSP carrying 70 % of a country was
rejected (lift 1 / 0.7 = 1.43). Traffic shares now come from the **baseline** period and `locus_lift` = **1.25**
(a sub-cohort above 80 % of its parent's traffic still cannot replace it).

**Consequences.** `COHORT_CONFIG_VERSION` = 3. Locus dimensions inherited from the Stage 3 scope are not removed
(not part of this decision). Thresholds still tuned on DEV only; HELDOUT untouched until Stage 9.

---

## ADR-037 — Stage 4 cohort intelligence design · *accepted* (2026-10-03, owner OK on Gate 0)

**Decision.** As in `docs/tasks/STAGE-4-DESIGN.md` (C-1 … C-10), with the owner's answers: (b) a daily cohort sweep
(`psp × customer_country`, `customer_country × payment_method_type`, BH q = 0.01 per day, candidates `S12`; dropped if
DEV shows more than 0.1 false positives per day from it); thresholds fixed before results (locus coverage 0.6, rate share
0.7 / 0.3, BH q = 0.05 per candidate); `volume_only` for count candidates whose locus approval did not move (|z| < 2).
Configuration is versioned in `src/anomalyos/cohorts/config.py` (`COHORT_CONFIG_VERSION = 1`).

**Clarifications made while implementing (no threshold changed).**
- Locus: the candidate's own scope is the coarsest eligible cohort, so "no cohort reaches 60 %" means the change is
  spread across the scope and the scope is the locus; ties on attempts prefer fewer dimensions, and a sub-cohort
  holding ≥ 98 % of the scope's attempts (e.g. psp_alpha = cards) is the scope itself.
- Composition parts are centred on the mean rate, ((r̄ᵢ − R̄)·Δwᵢ): the total is unchanged (ΣΔwᵢ = 0) and a mix shift
  is attributed to the cohort whose share moved and whose rate differs; ties go to the cohort whose share grew.
- Mix label: the smallest rate share across combinations with ≥ 2 supported cohorts (a composition artefact vanishes
  at the level that separates the mixing cohorts; a real rate change persists at every level).

**Known before evaluation (to be quantified at Gate 1).** A cohort that did not exist in the baseline (a new app
version) cannot be tested, so a version regression is localized only to the platform; at the version level it looks
like composition. `volume_only` on `refund_count` candidates is misleading (refund spikes do not move approval).

---

## ADR-036 — Claude Code as the primary coding agent · *accepted* (2026-10-03, owner spec "Claude Code Engineering Harness Optimization")

**Context.** Claude Code is the only coding agent used, but the coding harness still carried Cursor configuration
(`.cursor/rules/*.mdc`, `.cursor/skills/` planned paths, Cursor references in `AGENTS.md`), and parts of the `.mdc`
rules were stale (e.g. "only the simulator exists", "do not add pytest").

**Decision.**
- **`CLAUDE.md` is the entry point:** a short protocol (navigation, task protocol, test selection, final report) that
  imports `@AGENTS.md`; it does not duplicate the contract.
- **`AGENTS.md` stays the canonical, tool-independent engineering contract.** Cursor references removed; no rule or
  invariant changed.
- **`.claude/rules/`** holds contextual instructions with `paths:` frontmatter (architecture, stage-guard, testing,
  python, clickhouse, ai-safety). Claude Code loads a path-scoped rule when it works with a matching file, including
  reading it; rules link documents instead of copying them.
- **`.claude/skills/`** is where reusable procedures will live; only a README with conventions for now (ADR-004 still
  forbids empty Skills).
- **`.claude/settings.json`** holds minimal permissions: exact allow rules for the three harness checks and
  `git status`, `git diff`, `git branch --show-current`; deny rules for force pushes and reading `.env`. Everything
  else (commits, pushes, installs, Docker, other shell commands) keeps the standard confirmation prompt; no hooks,
  MCP servers or plugins.
- **`.cursor/` is removed.** Architecture tests now check the Claude Code configuration (`CLAUDE.md` imports
  `AGENTS.md` and stays under 200 lines, every rule is path-scoped, no empty Skills, valid settings).

**Verification of permission syntax (docs, 2026-10-03).** Wildcard allow rules such as `Bash(git diff *)` were
**not** added: Claude Code already runs read-only `git` forms without a prompt, and a wildcard rule would also allow
write-capable forms such as `git log --output=<file>`. Force-push deny rules cover the flag after `push` or after the
refspec and `+refspec`; per the documentation, Bash rules are not a security boundary (`sh -c`, absolute paths,
aliases bypass them), and `Read` deny rules do not stop a script that opens `.env` itself. The actual protection of
`main` is GitHub branch protection plus "the owner merges".

**Supersedes (only coding-agent configuration).**
- ADR-003: `.cursor/rules/*.mdc` as the scoped-rule mechanism → `.claude/rules/*.md`; its Cursor-specific context and
  consequence. Still in force: `AGENTS.md` is the contract, rules stay thin and do not paste it, no `.cursorrules`,
  no duplicate `CLAUDE.md` essay (the new `CLAUDE.md` is a short entry point), no marketplace Skills.
- ADR-004: the Skill path `.cursor/skills/<name>/SKILL.md` → `.claude/skills/<name>/SKILL.md`. Still in force: Skills
  are planned, not stubbed.

**Consequences.** No product code, invariant or dependency changes. A later Skill or rule follows `.claude/skills/README.md`
and the path-scoped rule format; the architecture tests fail on drift.

---

## ADR-035 — Stage 3 Gate 1: evaluation correction, coverage changes, frozen parameters · *accepted* (2026-10-03, owner OK)

**Context.** First DEV run (20 seeds × v1/v2, scale 1.0): main recall 0.78, 1.06 false positives per day; 28 % of
detections came from candidates opened *before* the incident began (a flaw in matching rule D-7).

**Decisions (owner OK, two rounds).**
- Evaluation: a candidate counts for a record only if `detected_at >= start`; during a `data_pipeline_issue` any
  first-look metric of the delayed cohort is a symptom of the delay.
- Coverage, not threshold tuning: hourly approval by country (S3h); `dunning_recovery_rate` (S5b);
  `subscription_cancellation_rate` v2 counting only voluntary cancellations (normalization 1.2.0 distinguishes
  `canceled_voluntary` / `canceled_involuntary`); S6 uses the new `refund_count` metric instead of the
  `refund_rate` ratio.
- Ground truth (sim-1.2.1, sim-1.2.2): `refund_rate` is no longer declared unchanged for approval incidents and the
  checkout regression — the same-window ratio rises mechanically when captures drop.
- Detector parameters stay as designed and are **frozen** (`DetectorConfig()`); `HELDOUT_SEEDS` untouched.

**Results (run 3).** Main: recall 0.89 (v1) / 0.87 (v2), 0.53 false positives per day, latency median 1 h, p90
16–19 h. Static threshold: recall 0.83, 6.2–7.6 false positives per day, p90 4–6 h. Details and limitations:
`docs/DETECTION.md`.

---

## ADR-034 — Stage 3 detection design · *accepted* (2026-10-02, owner OK on Gate 0)

**Decision.** As in `docs/tasks/STAGE-3-DESIGN.md` (D-1 … D-8), with the owner's answers: candidates that match
`suppress` records count as false positives; late data uses **first-look** visibility (`metrics.compute(...,
visibility="window_close")`: a window sees only events with `ingested_at <= window end`); the missing metrics are
added now (`subscription_cancellation_rate` v1, `late_arrival_share` v1 windowed by delivery time,
`checkout_conversion_rate` **v2** = converted as known when the checkout's window closed — v1 is unchanged, its
numerator sees authorizations up to the end of the whole range, which would leak future information into detection).

**Implementation choices recorded during Phase 1.**
- z is computed on variance-stabilized scales (arcsine for proportions, Anscombe for counts) instead of the plain
  normal approximation. Measured on stationary synthetic series: P(z < -2.5) = 0.0126 with the plain z vs 0.0065
  stabilized (nominal 0.0062); P(z < -4) 0.00039 vs 0.00006. Cause: binomial skew at approval ~0.9.
- Overdispersion: phi from the robust spread (median |z| / 0.6745)^2 of the last 168 unit-variance z, floored at 1,
  default 2 with fewer than 6 values. On 20 overdispersed series: 1 false alarm with phi, 84 without.
- Ratio metrics that can exceed 1 are clamped to [0, 1] for the transform only.
- Properties surfaced by the tests, to be quantified at Gate 1: (a) nothing is decidable in the first 3 days
  (fewer than two reference days), so incidents placed on days 0-2 cannot be detected; (b) the 30-attempt floor makes
  low-volume hours undecidable, which delays detection relative to the oracle (seed 42, scale 0.3: psp_alpha incident
  at 10:00 UTC detected at 13:00).

---

## ADR-033 — Full cause coverage, new mechanisms and metric expectations (sim-1.2, phases 5-6) · *accepted* (2026-10-02, owner OK on Gate 3)

**Decision.**
- Six scenarios join the `full` preset, so all 13 causes of vocabulary v1 occur: `dunning_failure` (retries fail, first attempts untouched; new oracle family `dunning_recovery_rate`), `pricing_or_plan_change` (new mechanism `CHURN`: cancellations at renewal, approval unchanged, exact counterfactual `extra_cancellations`), `data_pipeline_issue` (new mechanism `DELAY`: a share of a PSP's events gets a raw `delivered_at` 1-2 h late; payments unaffected; normalization takes `ingested_at` from it, `NORMALIZATION_VERSION 1.1.0`), `simultaneous_incidents` (two real incidents on disjoint cohorts, linked `unrelated_to`), `mix_shift_masking` (a cohort drop hidden in the global view by a US promotion), `ambiguous_signal` (`unknown`, route `watch`, the first `watch` record).
- Deviation from the Gate 3 sketch: `pricing_or_plan_change` models cancellations only, not extra refunds (refund behaviour stays with `refund_spike`).
- Deviation from ADR-029: with five long-running scenarios a 6 h gap between all of them no longer fits in 28 days (30 % placement failures). Long scenarios are now kept apart only within the renewal group (`subscription_renewal_failure`, `dunning_failure`, `pricing_or_plan_change`); long scenarios of different flows may overlap and are linked `unrelated_to`. Placement: 5,000/5,000 seeds.
- Every ground-truth record carries explicit `affected_metrics` and `unchanged_metrics` (negative evidence, e.g. checkout regression: affected `payment_intent_conversion_rate`, unchanged `charge_approval_rate`, `refund_rate`); the validator rejects overlaps. The control day gets reference `measured` values (whole-world attempts and approval).
- Side files `deployments.json` / `psp_status.json` are postponed to Stage 7 (owner decision).

**Consequences.** `GENERATOR_VERSION = sim-1.2.0` (with ADR-032). At seed 42 scale 1.0 every incident of the fixed calendar is oracle-distinguishable; at scale 0.3 the price change is not (excluded from recall per ADR-031).

---

## ADR-032 — Less clean baseline: realism v2 (sim-1.2, phase 4) · *accepted* (2026-10-02)

**Context.** Review 4.4: hourly approval was almost binomial and daily noise tiny, so false-positive rates on the simulator would be optimistic.

**Decision.** `WorldConfig.realism = "v1" | "v2"` (default v1, byte-identical). In v2: a (psp, country, hour) approval multiplier with sd `hourly_noise_sd = 0.035` (own random stream), a weekend factor 0.99 on local Saturday/Sunday, and 3-6 `benign_shock` records per run (x0.90-0.95 for 1-3 h on a random existing country x psp cohort; `normal_variation`, `suppress`; 6 h away from short scenarios and the control day). Calibrated on seed 42, scale 1.0, scenario-free world: hourly dispersion index per PSP 1.53-1.82 in v2 vs 1.23-1.43 in v1 (formula in `test_sim_realism.py`; the review's 1.12-1.23 used a slightly different estimator). The slow test checks v1 in [1.0, 1.6] and v2 in [1.45, 2.1] on seeds 1-3.

**Consequences.** Changing the default to v2 is a separate decision. New fields enter `run_id` only when non-default.

---

## ADR-031 — Oracle detectability in ground truth (sim-1.1, phase 3) · *accepted* (2026-10-02)

**Context.** `expected_detection_window` was a designer's constant (1 h, 4 h, 12 h, 3 days) unrelated to effect strength or cohort volume (review 4.3).

**Decision.** The engine keeps per-hour copies of its paired counters (actual vs counterfactual on identical random draws). Ground truth gets `oracle_detectable_at`: the end of the first hour, counted from `start`, in which the cumulative difference reaches z >= 3 one-sided (rates: binomial z of the actual count against the counterfactual rate; counts: Poisson z of the effect's excess over the cohort's organic baseline, counted at payment time), plus `oracle_method`. `null` if the effect never becomes distinguishable inside its window; always `null` for suppressed signals. `expected_detection_window` stays, with `basis: "designer_constant"`, as a lower bound of expectations. The benchmark measures detection latency from `max(start, oracle_detectable_at)`. **Owner decision (Gate 2):** incidents with `oracle_detectable_at = null` are excluded from recall (reported as a separate count, never scored as misses); weak randomized variants are kept as they are. The validator rejects an oracle time outside `[start, end]`.

**Consequences.** `GENERATOR_VERSION = sim-1.1.1` (event stream unchanged; ground truth gains fields). Findings at seed 42: the ES/psp_gamma drift becomes distinguishable after 69 h at scale 1.0 (110 h at 0.3); the BR issuer outage after 3 h (designer: 1 h) and not at all at scale 0.3; renewals after 2 h (designer: 12 h). Randomized DEV seeds 1-5 at scale 1.0: 54 of 55 incidents distinguishable; the exception is a weak iDEAL outage on seed 2. The oracle is optimistic for a real detector (it knows the cohort and compares with the counterfactual, not with a noisy baseline).

---

## ADR-030 — No cause labels in event data (sim-1.1, phase 2) · *accepted* (2026-10-02)

**Context.** Review finding 4.2: `issuer_not_available` was emitted only by incident effects (91 of 91 occurrences in seed 42), and effect-caused declines got a hidden `+8` risk score (24.4 vs 15.3 on average). A classifier could read the cause off a single event.

**Decision.** `issuer_not_available` joins the organic card-decline mix (weight 0.015 of 1.015). The `+8` boost is removed; elevated risk remains only for the injected card-testing stream (`risk_boost=30`). `EFFECT_ONLY_DECLINE_CODES = {stolen_card, highest_risk_level}`: codes that belong to an attack by definition. Tests: every other effect code occurs organically; mean risk score of declines inside vs outside incident windows differs by at most 1.0 (seed 42, scale 0.1, card testing excluded); `issuer_not_available` occurs outside incident windows.

**Consequences.** Generator output changes: `GENERATOR_VERSION = sim-1.1.0` (with ADR-029); golden digests updated in a separate commit.

---

## ADR-029 — Randomized scenario calendar (sim-1.1, phase 1) · *accepted* (2026-10-02, owner OK on the Gate 1 design)

**Context.** In sim-1.0 every seed replays the same incident times, cohorts and strengths, so many seeds only repeat noise and a detector can be tuned to the calendar.

**Decision.** `WorldConfig.schedule = "fixed" | "randomized"` (default `fixed`, byte-identical to sim-1.0). `simulation/schedule.py` is a pure function of the world: per scenario kind it draws start, duration, cohort (closed, reviewed lists of existing cohorts) and strength from `derive_seed(seed, "schedule", kind, attempt)`, placing kinds in a fixed priority order (long-running first, then the control day). Rules: windows inside the world (24 h warm-up, 6 h end margin); 6 h gap between short scenarios and between the three long-running ones (gradual drift, checkout regression, renewal failure); **long x short overlap allowed** and recorded both ways in `unrelated_to`; the control day touches nothing; the campaign + outage pair overlaps by design. The checkout-regression release (5.14.0) and hotfix (5.14.1, 36-72 h later, only on the affected platform) follow the scenario; 5.13.0 and 5.15.0 are jittered +/- 24 h; scheduled releases stay 48 h apart. New `WorldConfig` fields enter the `run_id` hash only when non-default. `seeds.py`: `DEV_SEEDS` 1-20, `HELDOUT_SEEDS` 1001-1020, `DEMO_SEED` 42.
**Deviations from the design table:** checkout-regression release days 5-14 (not 5-16: keeps 48 h after 5.13.0 and room for the long windows); gradual drift starts on days 16-22 at any hour.

**Consequences.** Placement succeeds on 5,000/5,000 seeds. Randomized mode targets scale 1.0; at small scales small-cohort incidents may be statistically invisible (phase 3 adds `oracle_detectable_at`). `GENERATOR_VERSION` is bumped together with phase 2.

---

## ADR-025 — Event normalization mapping (raw Stripe-shaped -> DATA_MODEL envelope) · *accepted* (2026-10-02, Stage 2)

**Status:** accepted for implementation in Stage 2; follows ADR-022 option A. Not reviewed by the owner beyond the brief in `docs/tasks/STAGE-2.md`; deviations from that brief are listed explicitly.

**Context.** Metrics, detection, cohorts and evaluation read one normalized layer (`<db>.events_norm`), produced by a pure, versioned mapping (`src/anomalyos/events/normalize.py`, `NORMALIZATION_VERSION = 1.0.0`, `schema_version = norm-1`).

**Decisions.**
1. **`ingested_at` = `occurred_at`** for sim-1.0.0: the raw stream has no ingestion delay. Never the wall clock. The column exists so late and out-of-order events are representable once the simulator emits them (`docs/tasks/SIMULATOR-FIXES.md`, phase 5); `as_of` filters use `ingested_at`.
2. **`merchant_id` = `mer_sim_001`**, constant, until multi-merchant.
3. **`run_id`, not `scenario_id`.** DATA_MODEL's envelope field `scenario_id` is renamed `run_id` (Stage 1 name). A *run* contains many scenarios; `scenario_id` already names a ground-truth record, so reusing it for the run would be ambiguous. `run_id` only partitions the table and is never a metric output or a detection/AI input (INV-015).
4. **`idempotency_key_present`** (outcome attribute, DATA_MODEL change): taken from the `payment_intent.created` request and inherited by that intent's charge events. Checkout intents carry a key (except duplicates); renewal intents never do (server-initiated), so duplicate-charge detection is defined on `channel = checkout`.
5. **Extra link columns** `customer_id` and `payment_intent_id` (needed for conversion and duplicate detection); **`attempt_no`** = ordinal of the charge on its PaymentIntent (retries and dunning reuse the intent), `0` for events that are not attempts.
6. **Mapping = the brief's table, with these deviations:**
   - `charge.refunded` is intentionally unmapped (a state change that duplicates `refund.created`); the brief did not list it.
   - `fraud.flagged` only for `outcome.reason = highest_risk_level`; `fraudulent` and `stolen_card` stay issuer declines (`payment.declined`).
   - `invoice.*` and `subscription.*` events carry `psp`, resolved through the renewal PaymentIntent's `invoice` (`invoice.created` has none: the PSP is chosen at the first attempt). Needed to split renewal success by PSP (found by the sanity test on the renewal-failure scenario).
   - `plan_id` is resolved from the subscription (invoice -> subscription, renewal PaymentIntent -> invoice).
   - `payment.attempted` and `chargeback.opened` stay in the vocabulary but are not produced (attempts are counted from authorized/declined/failed; no chargebacks are simulated).
7. **Strictness.** An unknown raw type, unknown `failure_code`, unexpected `subscription.updated` shape or a charge before its intent raises `NormalizationError`. Intentionally unmapped raw types are listed in code (`INTENTIONALLY_UNMAPPED`).
8. **Loading** is idempotent per `run_id` (drop partition + insert) and self-verifying (8 SQL checks, all 0): `anomalyos normalize --in <run_dir> [--replace]`, after `anomalyos-sim load`.

**Consequences.** `docs/DATA_MODEL.md` updated (envelope fields, `run_id`, new attributes). Any change to the mapping bumps `NORMALIZATION_VERSION`. Late events need a mapping change (a raw ingestion timestamp) when the simulator produces them.

---

## ADR-028 — Owner decisions on open questions OQ-6/7/8, invariants 15–16, versions, routing · *accepted* (2026-10-02)

**Status:** accepted by the owner (answers to the closeout open-questions round).

**Decisions.**
1. **Severity vocabulary (OQ-6).** One scale everywhere: the ground-truth scale `none / low / medium / high / critical` (`ground_truth.Severity`). Jev's severity `choice` ranges over `low…critical` (`none` means "not an incident", expressed by the incident `noul`). `P1/P2/P3` is a presentation label for the UI, defined by a mapping table in Stage 8; it never appears in Jev state, policy or ground truth. The simulator and its digests are unchanged.
2. **Mutable incident state (OQ-7).** ClickHouse, append-only: every status transition is a new row (`INV-010`); the current state is a view that takes the latest row per incident. No operational store and no new dependency for the prototype. Revisit if update/latency requirements outgrow it.
3. **Incident lifecycle (OQ-8).** The table in `docs/specs/07_INCIDENT_ENGINE.md`: `DETECTED → INVESTIGATING → ACKNOWLEDGED → ESCALATED → RECOVERING → RESOLVED | DISMISSED`; only a human reaches a terminal state; `RECOVERING → INVESTIGATING` when the signal returns. `docs/ARCHITECTURE.md` is aligned.
4. **Invariants.** `AGENTS.md` rules 16 and 17 become `INV-015` (ground truth is isolated) and `INV-016` (seeded, reproducible). Rules 18 (data conventions) and 19 (Jev typed questions) stay non-numbered rules backed by ADR-018/ADR-024. Both new invariants are test-enforced now (`tests/architecture/test_ground_truth_isolation.py`, `test_reproducibility_static.py`).
5. **Versions.** No V1/V2 split is introduced. "V2" keeps the meaning in `AGENTS.md`/`docs/ARCHITECTURE.md` (the whole current product generation: Mode A and Mode B, agent read-only). "Mode A is built first" is a stage order (`docs/specs/STATUS.md`), not a version.
6. **Routing and fallback.** Routes map `INCIDENT = incident`, `DIGEST = watch`, `IGNORE = suppress`. On Jev failure, timeout or unverifiable answer the route is `DIGEST`. Incident merge rule (overlapping windows, nested cohorts along approved chains, compatible metric families) is accepted as the working rule, refined with tests in Stage 6.

**Consequences.** `docs/specs` 03, 06, 07, `STATUS.md`, `ARCHITECTURE.md` updated. `INV-015`/`INV-016` added to `docs/INVARIANTS.md` and the catalog test. OQ-6/7/8 closed.

---

## ADR-027 — Model for human-readable incident explanations · *accepted* (2026-10-02, owner OK on the Gate 2 report)

**Status:** accepted — option (3), staged. Vendor and budget remain a separate ADR when a generative model is added.

**Context.** `docs/specs/00`, `08` and `INV-008` assume an "LLM explains" step. ADR-018 says Jev generates no text and that narrative explanation needs a separate generative model and its own ADR; the default there is a template rendered by code from typed answers. A generative model adds a dependency, a vendor, cost per incident, a new prompt-injection surface, and non-determinism.

**Options.**
- **(1) Templates only.** Code renders text from typed data and evidence IDs. No new dependency, deterministic, trivially satisfies `INV-008`. Cost: stilted prose, no synthesis across evidence.
- **(2) Generative model behind a port.** A narrow `Explainer` interface (input: validated evidence bundle with stable IDs, hypothesis records, timeline; output: structured claims each carrying evidence IDs and an epistemic label). A validator rejects uncited claims and unknown IDs (`INV-008`, `INV-013`). Vendor-neutral; a recorded-response fake for tests and evaluation. Cost: one new dependency (SDK or stdlib HTTP), API key handling, per-incident token budget, only typed fields in the prompt (no free text from data), replay fixtures.
- **(3) Both, staged.** Ship (1) with the (2) port defined at Stage 7; add a real model only after Mode A is benchmarked and there is a measured gap that templates cannot close (benchmark system "deterministic top-k + template" as the control).

**Recommendation.** (3). It keeps Stage 7 unblocked and dependency-free, defines the validator and the port (the hard, invariant-bearing part) first, and lets the benchmark show whether a generative model earns its cost. Choosing a vendor and a budget (tokens per incident, USD per run) is a separate ADR when (2) is built.

**Consequences if accepted.** No dependency change now. `docs/specs/08` and `00` change "LLM explains" to "an explainer behind a port; templated first".

---

## ADR-026 — Metric time grain per scope (resolves OQ-2) · *accepted* (2026-10-02, owner OK on the Gate 2 report)

**Status:** accepted — option (b). Numbers will be re-measured after `docs/tasks/SIMULATOR-FIXES.md` Phase 3 (randomised calendar, noisier baseline).

**Context.** Detection needs a grain per scope, and a minimum-sample floor. Measured on the synthetic world, `--seed 42 --scale 1.0` (run `run_133c4a3a198eb8c1`, 28 days, digests identical to the independent review): attempts = `charge.succeeded` + `charge.failed` events (retries included), all hours of all days, floor = 30 attempts per window. "Share" = fraction of cohort-windows (every cohort that has any attempt × every window, zero-attempt windows included) below the floor.

| Grain | Scope | Cohorts | Median attempts / window | Share < 30 |
|---|---|---|---|---|
| 5 min | global | 1 | 43 | 22.9 % |
| 5 min | PSP | 3 | 14 | 99.0 % |
| 5 min | PSP × country | 17 | 2 | 100 % |
| 15 min | global | 1 | 131 | 0 % |
| 15 min | PSP | 3 | 46 | 28.6 % |
| 15 min | PSP × country | 17 | 5 | 97.7 % |
| 15 min | PSP × country × platform | 68 | 1 | 100 % |
| 1 h | global | 1 | 532 | 0 % |
| 1 h | PSP | 3 | 189 | 0 % |
| 1 h | PSP × country | 17 | 21 | 63.3 % |
| 1 h | PSP × country × platform | 68 | 4 | 96.2 % |

(The independent review's 532 global / 183 PSP medians agree; its 32 % for "PSP × DE" is one large cohort, not the all-cohort share above.) Caveat: attempts are not independent trials (35 % of failures retry); rate uncertainty should use distinct payment intents or an overdispersion-aware interval, decided with the detector.

**Options.** (a) One grain for all scopes (15 min or 1 h). (b) Per-scope grain, series only where the floor holds. (c) Adaptive windows sized to reach the floor.

**Recommendation.** (b): global **15 min**; PSP **1 h**; every finer cohort (PSP × country and deeper) gets **no standalone detection series**: the cohort engine evaluates it pooled over the candidate incident window with a minimum-support gate (≥ 30 attempts, else `insufficient_data` and the cohort is not ranked). Add a daily series for slow drift (gradual degradation). Rationale: at 1 h, PSP × country is below the floor in 63 % of windows, so per-window alerts there would be mostly noise; pooling over an already-detected window is how the spec's cohort drill-down (spec 05) can work at all.

**Consequences if accepted.** Stage 3 builds series for global (15 min), PSP (1 h) and daily; Stage 4 consumes pooled counts. `docs/specs/03`, `04`, `05` get these numbers. Adaptive windows (c) stay a later option.

---

## ADR-023 — Single repository: harness and product runtime live together

**Status:** accepted (2026-10-02)

**Context.** Work existed in three places: this harness repo (docs, architecture tests, eval skeleton, "no runtime"), a nested, remote-less `anomalyos_simulator` git repo (Stage 0 foundation + Stage 1 synthetic world, with its own `CLAUDE.md` and ADR-001…013), and loose build specs. The two contracts conflicted (ADR-001, ADR-002, ADR-008 here forbid `src/`, dependency manifests, pytest and any ClickHouse client; the simulator needs all of them).

**Decision.** One repository, `markdragunov/anomalyos`. The simulator tree is imported as a single commit (no history preserved). Layout: `src/anomalyos/` (runtime), `tests/unit`, `tests/integration` (pytest, product), `tests/architecture` (stdlib unittest, harness), `docs/specs/` (build specs), `docs/tasks/` (current task briefs). `AGENTS.md` is the only agent contract; `CLAUDE.md` is a thin pointer to it. Simulator ADR-001…013 were renumbered to ADR-010…022 in this file (offset +9); code and docs were updated.

This ADR supersedes:
- **ADR-001** — runtime is now allowed, **stage by stage**: each new layer (metrics, detection, cohorts, Jev, incident engine, agent, API, UI) needs its Stage brief and, where it adds a dependency or top-level package, an ADR. Architecture tests were rewritten from "no product code" to "no code beyond the current stage" (forbidden layer filenames, dependency allowlist, forbidden top-level app directories stay enforced).
- **ADR-002** (partly) — the harness stays stdlib-only and runs without pip; the product uses `clickhouse-connect` (ADR-020) and `pytest` as the only dev dependency (ADR-011). A test pins the dependency allowlist so nothing is added without editing it and this log.
- **ADR-008** — ClickHouse is now provisioned (Docker Compose, ADR-012) and has a bounded client module (`anomalyos.simulation.clickhouse_load`, ADR-020).
- **ADR-009** (partly) — harness scripts remain Python 3.12 stdlib; product code targets Python ≥ 3.11.

**Consequences.** CI has two workflows: `harness.yml` (3.12, stdlib: architecture tests, architecture check, eval smoke) and `ci.yml` (product: ClickHouse service, pytest). The harness unittest run is scoped to `tests/architecture`; `tests/unit` and `tests/integration` run under pytest.

---

## ADR-024 — Jev "purity" vs a networked Jev (TypeSafe System One) · *accepted* (2026-10-02, owner OK on the Gate 2 report)

**Status:** accepted — option (a): `INV-004` unchanged for the decision contract, network call in a transport adapter with replay. Still no code may call a Jev endpoint before Stage 5; the `JevClient` port is designed then.

**Context.** `INV-004` (Jev is side-effect free: no I/O, no HTTP, pure function of structured input) was written for an abstract decision function. ADR-018 (formerly simulator ADR-009) defines Jev as TypeSafe's hosted System One model, which can only be reached over the network. As written, a client calling that API violates `INV-004`.

**Options.** (a) Keep `INV-004` for the *decision contract* (typed question set + state in, typed answers out; no tools, no writes, no hidden reads), and place the network call in a separate transport adapter behind it, with recorded-answer replay as the default in tests and evaluation. (b) Amend `INV-004` explicitly to allow exactly one outbound call (the model request) and nothing else. (c) Implement Jev locally as deterministic code and treat the hosted model as a baseline.

**Consequences per option.** (a) needs a `JevClient` port (typed request in, typed answers out), a transport adapter, and a replay format that stores the *raw* response, the question-set version and the returned model version (a state hash alone cannot reproduce a non-deterministic model); tests and CI use the replay/fake. `INV-004` text stays, with a clarification that purity applies to the decision contract. (b) edits `INV-004` and `AGENTS.md` rules 5 and 19 and the architecture tests in the same change; weaker guarantee, simpler code. (c) removes the external dependency and the cost, but is not "Jev"; the hosted model would be only a baseline.

**Recommendation.** (a) with replay. It preserves `INV-004`/`INV-005` for everything that matters (no side effects on billing state, no tools) and makes decisions reproducible. Until decided, no code may call a Jev endpoint, and `INV-004` is unchanged.

Related open points: ADR-018 says Jev does not generate text, so the "LLM explains" step needs a separate generative model and its own ADR; ADR-019 (formerly ADR-010, `proposed`) has Jev choose the tool from a closed set, while `docs/specs/08_INVESTIGATION_AGENT.md` has the agent choose the tool call.

---

## ADR-001 — Harness-only bootstrap; no product runtime

**Status:** superseded by ADR-023 (runtime now allowed stage by stage); was accepted (2026-09-28)

**Context.** `main` was an empty initialize commit. Agents need a contract before detectors, Jev, or ClickHouse exist.

**Decision.** Ship documentation, architecture tests, eval *structure*, CI that can actually run, and Cursor rules. Do not add `src/` product packages, drivers, LLM clients, or UI.

**Consequences.** Architecture tests assert the absence of product implementation. Stage 0 Product Foundation is a later, explicit change. Do not weaken those tests to sneak runtime in.

---

## ADR-002 — Standard library only for the harness

**Status:** accepted; partly superseded by ADR-023 (harness stays stdlib-only; product adds `clickhouse-connect` and `pytest`) (2026-09-28)

**Context.** Dependency policy: necessary? stdlib? coupling? reliability? deterministic tests?

**Decision.** Architecture tests and eval smoke use Python 3.12 **stdlib only** (`unittest`, `json`, `pathlib`, `re`, `time`). No `requirements.txt`, no pytest, no pydantic, no YAML library.

**Consequences.** Scenario files are JSON (not YAML) so parsing stays stdlib. GitHub Actions YAML is checked as text, not parsed with PyYAML. If a later stage needs a dependency, add it only after a new ADR.

---

## ADR-003 — `AGENTS.md` is the contract; Cursor rules stay thin

**Status:** accepted (2026-09-28); Cursor-specific parts superseded by ADR-036 (2026-10-03) — the rule mechanism is now `.claude/rules/`

**Context.** Cursor (2026) reads root `AGENTS.md` for all agents, and `.cursor/rules/*.mdc` for scoped project rules. Duplicating the contract into always-on rules wastes context and drifts.

**Decision.** Put the 15 rules, workflow, and principle in `AGENTS.md`. `.cursor/rules/` only contains focused reminders (safety pointer, docs, tests, no-product). No `.cursorrules`. No duplicate `CLAUDE.md` essay. No marketplace Skills.

**Consequences.** If Cursor behavior drifts, tighten a **scoped** `.mdc` rather than pasting `AGENTS.md` again.

---

## ADR-004 — Skills are planned, not stubbed

**Status:** accepted (2026-09-28); Skill path superseded by ADR-036 — `.claude/skills/<name>/SKILL.md`

**Context.** Empty `SKILL.md` files still get listed and pollute context. Procedures (ClickHouse, Jev, evals) do not exist yet.

**Decision.** Document future Skills in `docs/SKILLS.md`. Create `.cursor/skills/<name>/SKILL.md` only when the procedure is real.

**Consequences.** Coding agents follow `AGENTS.md` + docs until then.

---

## ADR-005 — Memory is files, not RAG

**Status:** accepted (2026-09-28)

**Context.** Need coding memory and future incident memory without a vector database.

**Decision.** Coding memory = git docs (this file, architecture, invariants). Incident memory = future structured records with stable evidence IDs. No embeddings, no RAG MCP.

**Consequences.** Agents must read docs rather than relying on unofficial chat memory. Incident search, when built, will be typed/filtered records, not similarity search over tickets.

---

## ADR-006 — CI runs only checks that exist

**Status:** accepted (2026-09-28)

**Context.** Eventual pipeline: PR → tests → lint → type checking → architecture checks → evaluation smoke.

**Decision.** Implement unittest + architecture check + eval smoke. Do **not** add fake `lint` / `typecheck` jobs (`exit 0`, echo skip). Introduce ruff/mypy (or equivalent) in a later ADR when there is product code to lint.

**Consequences.** The workflow file documents the eventual stages as comments only. `tests/architecture` asserts the workflow invokes the real commands.

---

## ADR-007 — Evaluation harness is structural

**Status:** accepted (2026-09-28)

**Context.** Need deterministic scenarios, ground truth, replay, baselines, benchmarks, traces, cost and latency — without anomaly detection.

**Decision.** JSON scenarios with required `ground_truth`. Smoke runner validates schema, writes a trace with `cost` and `latency_ms`, and does not score detectors. Baselines and benchmarks directories explain future capture; they do not contain invented detector scores.

**Consequences.** First real detector evals will add baselines in a dedicated change, not during harness bootstrap.

---

## ADR-008 — ClickHouse is documented SoT, not installed

**Status:** superseded by ADR-023 (ClickHouse provisioned, ADR-012/ADR-020); was accepted (2026-09-28)

**Context.** Analytical source of truth will be ClickHouse. Installing it now would add ops surface with no product.

**Decision.** Do not add Docker Compose, clickhouse-connect, or schemas. Document approved-interface rules so they exist before the first driver import.

**Consequences.** Architecture tests forbid ClickHouse drivers until an ADR explicitly allows a bounded client module.

---

## ADR-009 — Python as the harness language

**Status:** accepted; partly superseded by ADR-023 (harness on Python 3.12; product on Python ≥ 3.11) (2026-09-28)

**Context.** Need a test runner with zero dependencies. Runtime language is not frozen yet.

**Decision.** Use Python 3.12 for harness tests and scripts. Choosing Go/TS/Rust for the product later does not require rewriting these constraints; ports must preserve invariant IDs and eval JSON shape.

**Consequences.** Product code may live in another language. Architecture tests should then scan that language too (follow-up ADR).

---

# Decisions imported from the simulator tree

Originally ADR-001…013 of the former `anomalyos_simulator` repository, renumbered +9. Status and wording are preserved except where noted. Open questions (OQ-n) are listed at the end.

## ADR-010 — Standalone repository, separate from `ai-pm-case` · *accepted* (2026-10-02)

**Context.** The target repo `markdragunov/ai-pm-case` already contains a working project
(AI Recovery Engine, v0/v1/v2, passing tests) whose own CLAUDE.md forbids `pyproject.toml`,
dependencies and scope expansion. AnomalyOS needs all three.
**Decision.** Build AnomalyOS as its own repository. Do not modify `ai-pm-case`.
**Consequences.** Both projects keep coherent contracts. Confirmed by the owner: the single repository is `github.com/markdragunov/anomalyos`, which also
absorbs the former separate simulator tree (ADR-023).

## ADR-011 — Python 3.11+, `src/` layout, setuptools, pytest · *accepted*

**Context.** Need a standard, dependency-light, installable package.
**Decision.** `pyproject.toml` with setuptools backend; `src/anomalyos`; pytest as the only
dev dependency.
**Consequences.** `pip install -e ".[dev]"` is the whole setup. No lint tooling yet (add
with an ADR when a real need appears).

## ADR-012 — ClickHouse as analytical source of truth; local single node · *accepted*

**Context.** Billing analytics is append-heavy, aggregation-heavy, and dimension-sliced.
**Decision.** ClickHouse `25.8` (LTS) via Docker Compose, ports bound to localhost, named
volume. Same image pinned in CI.
**Consequences.** No production infra, clustering, or replication. Upgrades are explicit.

## ADR-013 — No ClickHouse client library in Stage 0 · *superseded by ADR-020*

**Context.** Stage 0 only needs a health check; choosing a client (e.g. `clickhouse-connect`)
before real query patterns exist is premature.
**Decision.** Use the HTTP interface via stdlib `urllib` for `/ping` and `SELECT version()`.
**Consequences.** Zero runtime dependencies. Stage 1 must decide the client with an ADR
(bulk insert performance and typed results are the criteria).

## ADR-014 — Configuration from process environment only · *accepted*

**Context.** Hidden `.env` loading makes behaviour depend on the working directory.
**Decision.** `load_settings(env)` is a pure function over a mapping; users export `.env`
explicitly; Docker Compose reads `.env` natively.
**Consequences.** Deterministic, easily testable config; one extra shell step for developers.

## ADR-015 — AI receives compact structured state; code owns policy · *accepted*

**Context.** Master context principles 2–9.
**Decision.** Jev and the agent never read raw events; inputs are versioned snapshots,
outputs are schema-validated; thresholds and lifecycle transitions live in code.
**Consequences.** Every AI component needs an input schema, output schema, validator,
fallback, and audit record before it ships.

## ADR-016 — Read-only agent; human-only resolution · *accepted*

**Decision.** The investigation agent uses allow-listed, typed, read-only tools and cannot
change incident state beyond attaching evidence. Only an authenticated human resolves or dismisses.

## ADR-017 — Ground truth stored separately from events · *accepted*

**Decision.** Scenario ground truth is derived from the scenario definition and stored where
no detection/AI component can read it; `scenario_id` is excluded from model inputs.
**Consequences.** Evaluation is non-circular.

## ADR-018 — Jev (TypeSafe System One) as the intelligence layer · *accepted*

**Context.** Jev is TypeSafe's System One model: one request evaluates a `state` against many
independent typed questions (`Choice`, `Score`, `Noul`) and returns probabilities, plus
confidence for Choice and Score. It does not generate text. Known weak spots (jev-1.13):
arithmetic, counting, date comparison, multi-hop indirection, large noisy state, literal
reading, adversarial content, no guaranteed cross-question identities.
**Decision.**
- Jev answers atomic judgments; code composes them (composite scoring) and owns weights and
  thresholds.
- Hypotheses come from a closed, versioned cause vocabulary; Jev ranks and rates, never invents.
- All numbers, counts and times are computed in code and passed as values or named buckets.
- States are small and filtered per question set; external free text is excluded by default.
- Confidence gates behaviour (act / flag / route to human), with thresholds per question and
  per risk level, calibrated on synthetic scenarios.
- No Jev-generated explanations; human-readable text is rendered by code from typed answers.
- Requests pin a model version for evaluation; the returned model version is always audited.
**Consequences.** The architecture's principles (compact state, code owns policy, auditable
AI) become natural rather than enforced after the fact. Assessment quality depends on the
quality of the question set, which becomes a first-class, versioned, tested artifact.
Narrative explanation, if ever needed, requires a separate generative model and its own ADR.

## ADR-019 — Investigation agent as a code-driven loop over Jev choices · *accepted, option 3* (2026-10-02, owner OK on the Gate 2 report)

**Context.** Jev selects; it does not plan or generate. The agent must be read-only and use
explicit tools.
**Decision.** The agent is a deterministic loop: code builds state, Jev chooses the next tool
and its closed-set arguments (or `stop`), code validates and executes, evidence is attached.
**Consequences.** Every step is auditable and bounded; no free-form tool arguments exist.
Revisit if investigation quality plateaus on scenarios that need open-ended exploration.

**Decision (accepted):** Option 3 below — the loop is code; code proposes a bounded, deterministically ranked shortlist of next steps; Jev chooses among them with a confidence gate. `docs/specs/08` is aligned to this.

**Conflict with `docs/specs/08` (Gate 2 analysis; option 3 accepted).** Spec 08: "the agent chooses the actual tool call" and Jev only rates hypotheses/sufficiency. This ADR: code builds state, Jev chooses the tool and closed-set arguments (or `stop`), code validates and executes.

- **Option 1 (this ADR).** Deterministic loop, Jev picks from a closed set. Auditable, bounded, replayable, no generative model in the loop, free-form tool arguments impossible. Cost: no open-ended exploration; selection quality is bounded by the question design.
- **Option 2 (spec 08).** A generative agent chooses tools. More flexible; needs a generative model in the loop (ADR-027 becomes a hard dependency), a larger injection surface and non-determinism, and harder `INV-011` budget enforcement.
- **Option 3 (hybrid).** Code proposes a ranked shortlist of next steps (deterministic top-k by contribution); Jev chooses among at most N options with a confidence gate; the loop is still code. Same guarantees as Option 1 with a stronger deterministic baseline.

**Recommendation.** Option 3 (a refinement of Option 1) for V2, with the benchmark control "deterministic top-k, no Jev" that the specs review asks for. Revisit Option 2 only if investigation recall plateaus on measured scenarios. If accepted, spec 08 is edited to match.

## ADR-020 — `clickhouse-connect` as the first runtime dependency · *accepted*

**Context.** Stage 1 loads ≈1.37M events per run. Hand-rolled `urllib` HTTP (ADR-013) would
need its own batching, compression, typed parameters and error handling. Resolves OQ-3.
**Decision.** Add `clickhouse-connect` (official client, Apache-2.0, HTTP transport, no native
build), pinned `>=0.8,<2`. Imported lazily and only by `anomalyos.simulation.clickhouse_load`;
generation and validation stay stdlib-only. The Stage 0 `doctor` health check keeps its
stdlib implementation. Inserts use `raw_insert(fmt="JSONEachRow")` in 50k-row batches.
**Alternatives.** stdlib `urllib` (no dep, more code); `clickhouse-driver` (native port 9000,
C extension); `numpy` for generation speed (not needed: ~1.5 min at scale 1.0).
**Consequences.** Supersedes the "no client library" clause of ADR-013. Transitive deps:
`urllib3`, `certifi`, `zstandard`, `lz4`, `pytz`. `chdb` (embedded ClickHouse) is used by one
unit test *if installed* to run DDL, loader and SQL checks without Docker; it is deliberately
not a declared dependency, and the test is skipped with a stated reason when absent.

## ADR-021 — Simulator: per-payment mechanisms, stream isolation, counterfactual truth · *accepted*

**Decision.** Scenarios inject mechanisms on individual payments (approval multiplier,
abandonment, refunds, duplicates, demand multiplier, exogenous stream), never metric
overrides. Every market-hour, effect-hour and renewal has its own RNG seeded from
`(seed, structural key)`; every PaymentIntent consumes a fixed-length uniform vector; IDs are
hashes of structural keys. Impact in ground truth comes from evaluating the same uniforms
with and without each effect (leave-one-out when effects overlap).
**Why.** Anomalies look organic in the events; a scenario perturbs nothing outside its
cohort/window (tested byte-for-byte); impact is exact and reproducible without any model.
Ground truth is derived from the scenario definition plus this counterfactual, never from the
emitted events (ADR-017) and lives in a separate database `<db>_truth`.
**Trade-offs.** Eager per-payment lifecycles cannot model feedback loops (customers churning
after failures changing future demand). Fixed UTC offsets, 30-day billing months.

## ADR-022 — Raw event format: Stripe-shaped source, DATA_MODEL envelope as normalized layer · *accepted, option A* (2026-10-02, owner OK on the Gate 2 report)

**Context.** Stage 1's spec asked for Stripe-shaped objects and an `evt_…` envelope
(`api_version`, `data.object` snapshot, `request.idempotency_key`). `DATA_MODEL.md` defines a
different logical envelope (`event_id`, `event_type` from its own vocabulary, `occurred_at`/
`ingested_at`, explicit dimensions, `scenario_id`). Metrics, detection and evaluation must be
written against exactly one of them.
**Proposal.** Keep both, as two layers with one direction of dependency:
1. *Raw layer* — Stripe-shaped events, as generated (realistic source format; immutable
   snapshots; idempotency signal needed for duplicate detection). Stored verbatim in
   `events.data`.
2. *Normalized layer* — a pure, versioned mapping raw → DATA_MODEL envelope
   (`charge.failed` → `payment.declined` or `payment.failed` by failure class, `invoice.*` →
   `invoice.*`, renewal PI → `subscription.renewal_attempted`, …) into a table the metrics layer
   reads. Metrics, detection, cohorts and evaluation depend **only** on this layer.
Today the loader already produces a flattened table with DATA_MODEL dimension names and
explicit `unknown`; the event-type mapping, `occurred_at`/`ingested_at` and `merchant_id` are
not implemented yet.
**Alternatives.** (a) Generate the DATA_MODEL envelope directly and drop Stripe shapes —
simpler, but loses realistic source semantics and forces a rewrite of Stage 1. (b) Adopt the
Stripe shape as the logical contract — rewrites DATA_MODEL and ties every layer to one
vendor's model.
**Status.** Accepted (option A). Next: ADR-025 (normalization mapping), written in Stage 2 Phase 2.

**Gate 2 summary (option A accepted).**
- **A (recommended).** Two layers: raw Stripe-shaped events stay as generated; a pure, versioned mapping produces the DATA_MODEL envelope; metrics, detection, cohorts and evaluation read only the normalized layer. Consequences: one more table (`events_norm`) and one mapping to test and version (the next ADR, reserved as ADR-025); keeps Stage 1 unchanged; the idempotency signal needed for duplicate-charge detection is carried as `idempotency_key_present` (a DATA_MODEL change); `ingested_at` and late/out-of-order events become representable only at the normalized layer.
- **B.** Generate the DATA_MODEL envelope directly. Simplest downstream, but rewrites Stage 1, changes every digest, and drops realistic source semantics.
- **C.** Make the Stripe shape the logical contract. Rewrites DATA_MODEL and ties every layer to one vendor's model.
- **Why A.** It is the only option that does not rewrite finished, validated work and that keeps the raw source realistic while giving the analytics layers a stable contract.

## Open questions

- **Reserved ADR numbers.** ADR-025 (event normalization) is written and accepted for Stage 2. ADR-026 (time grain) and ADR-027 (explanation model) are accepted above.
- **OQ-6 — Severity vocabulary.** Closed by ADR-028.
- **OQ-7 — Where mutable incident state lives.** Closed by ADR-028.
- **OQ-8 — Incident lifecycle states.** Closed by ADR-028.

- **OQ-1 — Jev integration details** (resolved in principle by ADR-018). Stage 5 status (ADR-039, ADR-040): stdlib HTTP
  transport stub, replay by default, key only in `.env` via `load_settings`; **still open:** provider access, endpoint
  and wire format, pinned model and its version metadata, limits, price, budget for the live run. Was open: official
  Python SDK vs stdlib HTTP client (dependency trade-off), pinned model version for
  evaluation, context-window budget per state, API key handling (`.env` only, never in git),
  and whether evaluation replays recorded answers by default (recommended).
- **OQ-2 — Time grain.** Default metric grain for detection (5 min / 15 min / 1 h) — decide
  with the first detector, based on synthetic volume.
- **OQ-3 — ClickHouse client.** Resolved by ADR-020.
- **OQ-4 — Country semantics.** Issuer country vs customer country vs merchant country for
  cohort decomposition; likely all three as separate dimensions. Stage 1 emits
  `customer_country` and `issuer_country` as separate columns (single merchant, so no
  merchant country yet); scenario cohorts use `customer_country`.
- **OQ-5 — Question-set design.** Closed by ADR-039 (question set v1, JevState v1 buckets). Was: the initial Jev question set and bucket edges; to be
  designed against scenarios 1, 10, 11 and 13 first (clear incident, slow drift, noise, recovery).
