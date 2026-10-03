# Task: Stage 5 — Jev Decision Layer, Verifier and Policy (Mode A)

Prepared: 2026-10-03  
Status: **Approved** by the owner (2026-10-03). Jev access: **none yet** — Stage 5 proceeds on the fake client (OQ-1 open)  
Branch: `stage-5-jev` (from `main` after PR #9)

## Read first

`AGENTS.md` → `docs/specs/06_JEV_INTELLIGENCE.md` (including amendments) → `docs/COHORTS.md` → `docs/DETECTION.md` → `docs/DATA_MODEL.md` (JevState, cause vocabulary, severity, routes, semantic encoding) → `docs/ARCHITECTURE.md` (§ AI boundaries, § 6 Jev layer, § 7 policy) → `docs/DECISIONS.md` (ADR-018, ADR-019, ADR-024, ADR-027, ADR-028, ADR-037, ADR-038, OQ-1, OQ-5) → `docs/INVARIANTS.md` (INV-004, INV-005, INV-006, INV-009, INV-010, INV-013, INV-015, INV-016).

Work in phases. Stop and report at every ⛔ Gate.

Every phase report must include:

- What changed
- Decisions and ADR IDs
- Tests: passed / failed / skipped, with reasons
- Measurements
- Open questions
- Explicit statement whether the next phase is authorized

---

## Purpose

Turn each promoted Stage 3 candidate, together with its Stage 4 analysis, into one deterministic route:

- `IGNORE`
- `DIGEST`
- `INCIDENT`

The decision pipeline:

1. Code builds a bounded, typed `JevState`.
2. Jev answers a versioned set of structured questions.
3. A deterministic verifier validates the response.
4. A deterministic, versioned policy selects the route.
5. Code assembles an auditable decision record.

**Jev decides. Code verifies and routes.**

Stage 5 introduces no text generation, no tools for Jev, no investigation agent, no incident grouping and no incident lifecycle.

The system must remain reproducible, auditable, bounded and safe when Jev is unavailable or returns invalid output.

## What Stage 5 inherits

All decisions must be evaluated against the actual limitations of Stage 3 and Stage 4, not idealized inputs.

### Stage 3 — DEV, randomized calendar

- Recall: 0.89 / 0.87.
- False positives: 0.53 per day.
- `realism_v1`: 266 false-positive candidates across 20 worlds:
  - 177 matched no record;
  - 52 matched suppress records, including seasonality, campaigns and mix-shift masking;
  - 37 affected a declared unchanged metric.

These candidates should generally end as `IGNORE` or `DIGEST`, not `INCIDENT`.

### Stage 4 — DEV

- Top-1 exact locus: 44–45%.
- Equivalence-aware locus: 61%.
- Rate-vs-mix labels (`rate_change` / `mix_shift` / `mixed` / `new_cohort`) correct for 82–83% of incidents.
  Stage 4 produces no cause labels; the first cause (`category`) comes from Jev in Stage 5.
- Known gaps are documented in `docs/COHORTS.md`:
  - scope dimensions in the locus;
  - attribution between simultaneous incidents;
  - impact accuracy.

Jev must be evaluated using these actual Stage 4 outputs.

Do not repair, reinterpret or silently improve Stage 3/4 inputs inside Stage 5.

---

# Before Gate 0 — Owner access and constraints

Jev is TypeSafe's hosted System One model (ADR-018, ADR-024).

The repository currently has no confirmed endpoint, key, SDK choice or pinned model version. OQ-1 remains open.

The design can proceed without live access. Live Jev evaluation cannot.

The owner must provide or explicitly defer:

- API documentation and access;
- endpoint and authentication requirements;
- model identifier and pinning capabilities;
- returned model-version metadata;
- rate limits;
- pricing model;
- request and response size limits;
- timeout constraints;
- maximum budget for the DEV live run;
- approval for live calls.

The API key must remain in `.env` or an approved secret mechanism:

- Never commit the key.
- Never expose the key in logs, decision records or replay artifacts.
- Never read `.env` from the coding agent.
- Live calls require explicit owner approval.
- CI must never call the live endpoint.

If the provider cannot guarantee an immutable model version, document the limitation. A requested model alias is not proof that the underlying model is immutable.

---

# Phase 0 — Design ⛔ Gate 0

**No product implementation before explicit owner approval of Gate 0.**

Create a design note covering the following contracts.

## 0.1. Input contract and JevState

### Canonical input

The state builder receives:

- the promoted `AnomalyCandidate` (Stage 3);
- `CohortAnalysis`;
- `EvidenceBundle`;
- linked candidate references where available.

`IncidentCandidate` (`docs/DATA_MODEL.md`) does not exist as a code type; it arrives with incident grouping in Stage 6.

`EvidenceBundle` is the canonical analytical input for the JevState builder.

`JevStateBuilder` is the only component allowed to transform candidate and Stage 4 analysis data into model-facing state.

Do not pass the same analytical information to Jev through multiple parallel representations.

The design note must define:

- the source of truth for every state field;
- precedence when source values conflict;
- fields intentionally excluded from JevState;
- handling of missing or contradictory inputs;
- handling of linked candidates;
- maximum serialized state size;
- deterministic behavior when the size budget is exceeded.

Do not silently truncate state. Use deterministic reduction rules or reject the state as unverified.

### State representation

JevState must use:

- typed fields;
- named semantic values;
- versioned buckets;
- closed enums where applicable;
- explicit `unknown` / `not_provided` values where evidence is unavailable.

Candidate fields should cover, where supported by Stage 3/4:

- relative change;
- statistical strength;
- onset;
- share of change explained by the locus;
- rate-versus-mix interpretation;
- impact size;
- observed recovery state;
- related-metric agreement;
- calendar context.

Bucket edges and semantic definitions must be defined in code and versioned.

Use the existing semantic encoding principles in `docs/DATA_MODEL.md`.

JevState must not contain:

- free text;
- raw timestamps;
- `run_id`;
- `scenario_id`;
- seed or simulator metadata;
- ground truth;
- raw event payloads;
- unrestricted customer or payment identifiers;
- arbitrary SQL or query text.

Exact analytical values remain in code-owned candidate/evidence structures and audit/evaluation data, not in the model-facing state unless explicitly approved by the spec.

### State size

Define a hard serialized-size budget before implementation.

Report:

- maximum bytes;
- maximum tokens, if provider tokenization is available;
- observed size distribution on DEV candidates;
- behavior at and above the limit.

The budget must be enforced before the network request.

### Evidence provenance

Use a code-owned side map:

`state_field -> evidence_ids`

Do not ask Jev to generate, select or invent evidence IDs.

Examples:

- `relative_change_bucket` → candidate metric evidence;
- `locus_share_bucket` → cohort decomposition evidence;
- `impact_bucket` → impact evidence;
- `related_metric_agreement` → related metric evidence IDs.

The builder produces both:

1. `JevState`;
2. `EvidenceProvenance`.

Jev receives the semantic state, not the provenance map.

The decision record stores:

- all evidence IDs used to build the state;
- the state-field provenance map;
- the state schema version;
- the state hash.

The verifier may validate evidence references against the code-owned provenance map. It must never invent missing evidence.

## 0.2. Time, freshness and canonical serialization

Separate three concepts.

### JevState

Contains only semantic time buckets, not raw timestamps.

### Decision context

Code supplies explicit time inputs, including:

- `evaluated_at`;
- `as_of`;
- candidate observation window;
- freshness limit.

The verifier must not read the system clock directly.

### Audit record

Stores the timestamps used for freshness validation and the actual request/response times.

All time inputs must be explicit so replay is deterministic.

Define canonical serialization for state hashing:

- stable field ordering;
- stable enum representation;
- explicit null representation;
- numeric normalization;
- UTF-8 encoding;
- schema version included in the hash input.

The same logical state and schema version must produce the same hash.

## 0.3. Question set v1 — OQ-5

Mode A triage only.

Each question must have:

- stable question ID;
- version;
- exact semantic definition;
- response type;
- allowed values or numeric range;
- wording rules;
- policy role;
- evaluation target;
- ablation plan.

Use the response primitives defined by ADR-018:

- `noul`: a probability for one proposition;
- `choice`: a selection over a closed option set, with the defined probability/confidence structure.

Do not introduce an undocumented generic confidence field.

### Required v1 questions

| ID | Type | Semantic meaning | Policy role |
|---|---|---|---|
| `is_incident` | noul | Probability that the candidate represents a real operational incident rather than normal variation | Primary incident gate |
| `severity` | choice | Severity conditional on the candidate being a real incident | Risk threshold for INCIDENT |
| `category` | choice | Most plausible cause from the closed cause vocabulary v1 | Classification and audit; not a standalone page gate |
| `needs_human` | noul | Probability that a human should review the candidate | Can prevent automatic INCIDENT and route to DIGEST |

`severity` must use the closed severity vocabulary already defined in `DATA_MODEL.md`.

`category` must use the closed cause vocabulary v1. No free-form categories.

### Recovery

Do not make a separate recovery prediction mandatory in v1.

Distinguish:

- `observed_recovery`: a code-derived fact from detection/cohort inputs;
- `recovery_likelihood`: a model prediction about future or ongoing recovery.

The first may be included in JevState if supported by upstream evidence.

The second requires a separately defined target, observation horizon and evaluation method. Defer it unless Gate 0 explicitly approves those definitions.

### Optional questions

Per-cause plausibility and novelty are not required in v1.

Add them only if:

- their semantic target is unambiguous;
- they do not duplicate `category`;
- they have a measurable evaluation target;
- their additional cost is included in the budget.

### Required scenario coverage

Design and test question semantics against:

- clear incident: PSP degradation;
- slow drift;
- noise: small cohort or benign shock;
- recovery;
- healthy mix change: seasonality or campaign.

### Question ablations

Specify before results which questions will be ablated.

An ablation must distinguish:

- removing a question from the request;
- removing a question's answer from policy inputs.

These are different experiments.

If an ablation requires a new live request, count it in the live-call budget.

Do not claim that answer removal measures the effect of removing the question from the model prompt.

## 0.4. JevClient port and transport — ADR-024

Define a typed client boundary.

Request includes:

- question-set version;
- JevState and schema version;
- requested pinned model identifier;
- explicit request metadata required for replay.

Response includes:

- typed question answers;
- returned model identifier/version;
- provider request identifier where available;
- structured transport/model error where applicable.

Keep the transport adapter in its own module.

Use stdlib HTTP unless an SDK is justified by an ADR.

Requirements:

- explicit timeout;
- no automatic retry loop;
- structured error types;
- bounded request/response size;
- no secrets in logs;
- no network I/O outside the transport module.

### Model pinning

The verifier must validate returned model metadata against the approved pinning policy.

If the provider returns a different version or cannot provide sufficient version metadata:

- mark the response `unverified`;
- route to `DIGEST`;
- record the limitation.

Do not silently accept a changed model alias as the same model version.

## 0.5. Replay and execution modes

Support three modes:

### `replay`

- Default in tests, CI and evaluation.
- Uses previously recorded responses.
- Must reproduce the same typed response and decision from the same inputs and versions.

### `record`

- Performs live provider calls.
- Requires explicit owner approval.
- Stores raw provider response and replay metadata.
- Must obey the budget guard.

### `fake`

- Used for isolated unit tests.
- Returns deterministic fixtures.
- Must not be presented as a live Jev evaluation.

Replay records must include:

- raw provider response;
- request hash;
- state hash;
- state schema version;
- question-set version;
- requested model version;
- returned model version;
- transport version;
- provider request ID where available;
- response timestamp;
- replay artifact version.

Secrets must be redacted before persistence.

Settings must be loaded through `load_settings`.

## 0.6. Verifier contract

The verifier is deterministic and has no network access.

It validates:

- response schema;
- exact required question IDs;
- duplicate or missing question IDs;
- response cardinality;
- option membership;
- numeric ranges;
- probability consistency within each primitive;
- no unsupported cross-question probability identities;
- required state/evidence provenance references;
- state hash;
- state schema version;
- question-set version;
- requested and returned model metadata;
- response freshness using explicit time inputs;
- response and payload size limits.

The verifier must not:

- repair malformed answers;
- infer missing answers;
- invent evidence IDs;
- call Jev again;
- access ground truth;
- read the system clock directly.

Any validation failure results in `unverified`.

All validation failures must have structured reason codes suitable for audit and evaluation.

## 0.7. Policy contract

Implement two distinct policies and one operational failure behavior.

### A. Jev policy

A deterministic, versioned policy consuming:

- verified Jev answers;
- Stage 3/4 typed outputs;
- explicit policy configuration.

It returns exactly one route:

- `IGNORE`;
- `DIGEST`;
- `INCIDENT`.

Define thresholds per question and, where required, per risk level.

Define the interaction of:

- `is_incident`;
- `severity`;
- `needs_human`;
- `category`;
- Stage 3/4 signal strength;
- locus share;
- impact bucket.

The policy must have an explicit decision table for:

- low incident probability;
- uncertainty band;
- high incident probability with low severity;
- high incident probability with critical severity;
- high `needs_human`;
- unknown category;
- contradictory or incomplete Stage 3/4 evidence;
- candidate with insufficient locus support.

`category` must not independently authorize or suppress an INCIDENT route.

A wrong or unknown cause label must not automatically hide a genuine incident.

`needs_human` above its approved threshold should route to `DIGEST`, unless a separately approved safety rule defines otherwise.

All threshold values must be versioned and recorded.

Do not tune thresholds during implementation.

### B. No-Jev baseline policy

Implement a deterministic baseline using only Stage 3/4 outputs:

- statistical strength;
- candidate label;
- locus share;
- impact bucket;
- other explicitly approved typed fields.

This is a benchmark control used to compare Jev against a deterministic alternative.

It is not the operational fallback for provider failure.

Evaluate it on exactly the same candidates and matching records as Jev.

### C. Operational failure fallback

For any of the following:

- provider/model error;
- timeout;
- verifier failure;
- missing or stale response;
- model-version mismatch;
- exhausted live-call budget;

the route is always `DIGEST`, according to ADR-028.

This behavior is independent of the no-Jev baseline.

Every fallback must produce an audit record with a structured failure reason.

No automatic retry loop.

### Policy determinism

For identical:

- state;
- verified answers;
- Stage 3/4 inputs;
- policy version;
- explicit time inputs;

the policy must always return the same route.

No network calls, system-clock reads or hidden mutable state.

## 0.8. Assessment and audit

`Assessment` is assembled in code from typed, verified answers.

Do not ask Jev to generate a narrative assessment.

Create a decision record per candidate.

The record must include the applicable fields from spec 06 and INV-010, including:

- decision ID;
- mode;
- candidate reference;
- state hash;
- state schema version;
- question-set version;
- requested model version;
- returned model version;
- transport version;
- raw or normalized answers, according to the approved storage contract;
- verifier status and failure reason;
- policy version;
- route;
- evidence IDs;
- evidence provenance map;
- explicit evaluation timestamps;
- request and response timestamps;
- actor / initiator;
- budgets consumed;
- provider request ID, where available.

Do not put `scenario_id`, seed or ground truth into JevState or Jev/policy runtime interfaces.

Evaluation may associate decisions with ground truth through a separate evaluation-only mapping.

### Storage decision

Gate 0 must choose and document the storage boundary.

Separate:

1. runtime decision audit;
2. raw provider response/replay artifacts;
3. evaluation metadata and ground truth.

If decision records are runtime audit, use an append-only ClickHouse table consistent with ADR-028 and the approved architecture.

If files are used temporarily, explicitly classify them as evaluation/replay artifacts, not production runtime audit.

Any ClickHouse schema change requires an ADR.

Ground truth must remain inaccessible to detection, JevStateBuilder, Jev, verifier and policy, in accordance with INV-015.

## 0.9. Evaluation protocol — fixed before results

Evaluation uses DEV seeds only. HELDOUT remains untouched.

Use the existing Stage 3 candidate-to-record matching logic. Do not silently change matching semantics inside Stage 5.

For each candidate, evaluation must produce exactly one matching status:

- matched incident;
- matched watch;
- matched suppress;
- unmatched.

Document precedence for overlapping records and behavior when multiple candidates match one incident.

Evaluation-only identifiers must remain outside the Jev/policy runtime boundary.

### Candidate-level routing metrics

Because incident grouping and lifecycle belong to Stage 6, Stage 5 must report candidate-level dispositions, not claim to measure real pages or grouped incidents.

Report:

- `INCIDENT` routes per day;
- `DIGEST` routes per day;
- `IGNORE` routes per day;
- route distribution;
- matched incident candidates routed to `INCIDENT`;
- unmatched candidates routed to `INCIDENT`;
- suppress candidates routed to `INCIDENT`;
- watch candidates routed to `INCIDENT`;
- number of `INCIDENT` routes per matched ground-truth incident.

Use the term `candidate-level INCIDENT routes`, not actual pages or incident count.

### Classification and calibration

Report:

- route accuracy against `expected_route`;
- incident recall at the `INCIDENT` route;
- severity accuracy against ground-truth severity for matched incident candidates;
- category top-1 and top-3 against `true_cause` for matched incident candidates;
- calibration of `is_incident` against ground-truth `is_incident`;
- reliability diagram and Brier score;
- digest volume;
- verifier failure rate;
- provider failure rate.

Do not use `expected_route == incident` as a substitute for the ground-truth `is_incident` calibration target.
Ground-truth `is_incident` is defined as `expected_route != suppress`: it is true for incident **and** watch records.

### Comparison

Compare:

- Jev policy vs no-Jev baseline;
- question-set versions where an additional version has been explicitly approved;
- scenario kinds;
- realism/simulator versions separately from question-set versions.

Use unambiguous version names:

- `realism_v1`, `realism_v2`;
- `question_set_v1`, `question_set_v2`.

Do not use generic `v1` / `v2` labels.

### Tuning and validation

Do not tune and report final performance on the same DEV seeds without qualification.

Split DEV seeds into:

- tuning subset;
- validation subset.

Tune thresholds only on the tuning subset. Report final DEV validation metrics on the separate validation subset.

If the available seeds do not support a meaningful split, use a documented leave-one-seed-out procedure.

Stage 3 and Stage 4 parameters were already tuned on all 20 DEV seeds (ADR-035, ADR-038). The validation subset is
therefore clean only for Stage 5 thresholds, not for the whole pipeline; report it with that qualification.

HELDOUT must not be inspected, tuned against or used to select thresholds.

### Cost and operational metrics

Report:

- live calls;
- replay calls;
- fake calls;
- latency distribution;
- provider errors;
- verifier failures;
- token/request size where available;
- actual price;
- estimated cost per candidate;
- total run cost;
- budget utilization;
- ablation overhead.

Keep provider cost separate from internal replay/evaluation compute.

## 0.10. Cost bound and budget guard

The initial estimate is approximately 50 candidates per world and approximately 2,000 calls for 40 DEV worlds.

This is an estimate, not an approved budget.

Before live evaluation, the owner must approve:

- maximum calls;
- maximum total cost;
- maximum cost per candidate;
- rate limit;
- timeout;
- maximum request/response size;
- additional ablation budget.

Implement a deterministic budget guard before transport invocation.

When the budget is exhausted:

- do not call the provider;
- route to `DIGEST`;
- record `budget_exhausted`;
- do not retry.

The live-call budget must include all approved ablations and repeated experiments.

## 0.11. Calendar context

Do not add new calendar, campaign, deployment or external integrations in Stage 5.

Only use calendar context already present in approved Stage 3/4 typed inputs.

If no reliable source exists, use `unknown` / `not_provided`.

Any future source must have a typed interface and provenance.

Never use simulator ground truth or scenario metadata as calendar context.

## 0.12. Scope boundary

Stage 5 implements Mode A only.

Out of scope:

- Mode B drilldown questions;
- investigation agent;
- incident grouping;
- incident lifecycle;
- generated explanations;
- UI;
- billing-state mutations.

Mode B importance and shortlist questions belong to the investigation loop in Stage 7, consistent with ADR-019 option 3.

Incident grouping belongs to Stage 6.

---

# Phase 1 — Implementation

Only start after explicit Gate 0 approval.

Implement:

`src/anomalyos/jev/`

- typed JevState;
- state builder;
- semantic buckets;
- evidence provenance;
- question-set definitions;
- JevClient port;
- transport adapter;
- replay store;
- fake client;
- verifier;
- assessment assembly.

`src/anomalyos/policy/`

- versioned policy configuration;
- Jev policy;
- no-Jev baseline policy;
- route definitions;
- decision records;
- operational failure fallback.

Remove `jev` and `policy` from the stage guard in the same change.

Do not introduce future-stage packages or abstractions.

### Required tests

#### JevState

- bucket edges;
- deterministic serialization and state hash;
- state size limit;
- deterministic size-limit behavior;
- no free text;
- no raw timestamps;
- no run/scenario/seed identifiers;
- no ground truth;
- evidence provenance correctness;
- unknown/missing input behavior.

#### Verifier

Reject each malformed response class:

- missing question;
- duplicate question;
- unknown question ID;
- invalid option;
- invalid numeric range;
- inconsistent probability primitive;
- invalid cardinality;
- missing evidence provenance;
- mismatched state hash;
- mismatched schema/question-set version;
- missing or mismatched model metadata;
- stale response;
- oversized response.

No verifier test may depend on live Jev.

#### Policy

- deterministic routing;
- all decision-table boundaries;
- contradictory answers;
- unknown category;
- low/high severity combinations;
- `needs_human` threshold;
- unverified response;
- model error;
- timeout;
- budget exhaustion.

All operational failures must route to `DIGEST` and create an audit record.

#### Replay and transport

- replay reproduces the same typed response and decision;
- fake client is deterministic;
- no retry loop;
- secrets are redacted;
- transport timeout produces structured error;
- only the transport module can perform network I/O.

#### Architecture and safety

- Jev and policy cannot read ground truth;
- Jev has no tools, I/O or side effects;
- policy is deterministic;
- JevStateBuilder cannot access evaluation-only metadata;
- CI never calls the live endpoint;
- no new dependencies without an ADR.

Run:

```bash
python3 -m unittest discover -s tests/architecture -t . -v
python3 scripts/check_architecture.py
python3 scripts/eval_smoke.py
```

Also run the relevant product/unit/integration tests.

Report skipped tests with an explicit reason. Do not silently skip failures.

---

# Phase 2 — DEV Evaluation ⛔ Gate 1

Only start live evaluation after:

- Gate 0 approval;
- owner approval for live calls;
- model and budget configuration recorded;
- tuning/validation split fixed;
- evaluation protocol committed.

Record Jev answers on DEV using the approved live configuration.

Replay the recorded responses for all subsequent analysis.

Report:

- Jev vs no-Jev baseline;
- route metrics;
- incident recall at candidate-level `INCIDENT`;
- unmatched/suppress/watch candidates routed to `INCIDENT`;
- severity and category accuracy;
- calibration;
- question ablations;
- per-scenario-kind results;
- realism version;
- question-set version;
- latency;
- cost;
- provider and verifier failure rates.

Thresholds may be tuned only on the DEV tuning subset.

Report final DEV validation metrics on the separate validation subset.

Do not access HELDOUT.

### If Jev access is unavailable

Complete the technical pipeline using the fake client and replay fixtures where possible.

Report:

- baseline policy results;
- fake-client pipeline results;
- technical verifier/policy/replay test results;
- `Jev evaluation: blocked — no live access`.

Do not describe fake-client results as Jev model evaluation.

Gate 1 cannot be approved as a successful model-quality evaluation without live Jev results.

---

# Phase 3 — Documentation and PR

Create or update:

- `docs/DECISIONING.md` — state, provenance, question set, verifier, policy, fallback, audit, replay and evaluation results;
- `docs/DECISIONS.md` — Gate 0 and Gate 1 decisions;
- `docs/INVARIANTS.md` — only if an approved invariant change is required;
- stage tables and task status.

Record the approved question set as the resolution of OQ-5.

Record provider/model access and pinning constraints as the resolution or explicit remaining status of OQ-1.

Create PR:

`stage-5-jev` → `main`

The PR must remain open and unmerged.

No unrelated Stage 3 or Stage 4 changes.

---

# Out of scope

- Stage 6 incident engine and grouping;
- Stage 7 investigation agent and Mode B;
- generative explanations (ADR-027);
- UI;
- HELDOUT evaluation;
- new dependencies without an ADR;
- writes to billing state;
- new calendar/campaign/deployment integrations;
- automatic provider retries;
- arbitrary SQL generation;
- autonomous incident lifecycle actions.

---

# Acceptance criteria

## Gate 0

- [ ] Owner access and provider constraints recorded, or explicitly marked unavailable.
- [ ] JevState schema and source-of-truth mapping approved.
- [ ] State size budget and deterministic overflow behavior approved.
- [ ] Evidence provenance side map approved.
- [ ] Canonical serialization and state hash contract approved.
- [ ] Question set v1 approved and recorded against OQ-5.
- [ ] Recovery semantics explicitly resolved or deferred.
- [ ] Verifier contract approved.
- [ ] Jev policy decision table approved.
- [ ] No-Jev baseline defined as benchmark control, not failure fallback.
- [ ] Operational failure behavior fixed to `DIGEST`.
- [ ] Model pinning and mismatch behavior approved.
- [ ] Audit/replay/evaluation storage boundaries approved.
- [ ] DEV tuning/validation split and evaluation protocol approved.
- [ ] Live-call and cost budget approved.
- [ ] Gate 0 ADRs recorded.
- [ ] Owner explicitly authorizes Phase 1.

## Phase 1

- [ ] JevState is typed, bounded, bucketed and free of free text and ground truth.
- [ ] State hash is deterministic.
- [ ] Evidence provenance is code-owned and auditable.
- [ ] Verifier rejects every defined malformed response class.
- [ ] Jev policy and baseline are deterministic and versioned.
- [ ] All operational failures route to `DIGEST`.
- [ ] All decisions and failures produce audit records.
- [ ] No automatic retry loop.
- [ ] Replay is the default in tests, CI and evaluation.
- [ ] CI never calls live Jev.
- [ ] Only transport module performs network I/O.
- [ ] Jev and policy cannot access ground truth.
- [ ] No invariants weakened.
- [ ] No unrelated runtime changes or new dependencies.
- [ ] Architecture, product and evaluation smoke tests pass.

## Gate 1

- [ ] Live DEV run completed with owner approval, or explicitly blocked by unavailable access.
- [ ] Jev and no-Jev baseline evaluated on the same candidates.
- [ ] DEV tuning and validation results reported separately.
- [ ] Candidate-level route metrics reported.
- [ ] Severity/category/calibration results reported.
- [ ] Ablations reported or explicitly deferred with cost reason.
- [ ] Provider failures, latency and actual cost reported.
- [ ] HELDOUT remains untouched.
- [ ] Owner explicitly approves the evaluation outcome.

## Phase 3

- [ ] `docs/DECISIONING.md` complete.
- [ ] ADRs and stage tables updated.
- [ ] Gate 0 / Gate 1 decisions recorded.
- [ ] Harness and product tests green locally and in CI.
- [ ] PR `stage-5-jev` → `main` open and not merged.
- [ ] No unrelated Stage 3/4 changes.

**Do not proceed past any Gate without explicit owner approval.**