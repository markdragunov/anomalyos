# Decisioning (Stage 5): JevState, Jev, verifier, policy

Turns every promoted Stage 3 candidate, with its Stage 4 analysis, into exactly one route — **`IGNORE` / `DIGEST` /
`INCIDENT`** — and one audit record. **Jev decides. Code verifies and routes.** No generated text, no tools, no
grouping or lifecycle (Stage 6). Code: `src/anomalyos/jev/`, `src/anomalyos/policy/`. Decisions: ADR-039 (design,
Gate 0), ADR-040 (Gate 1). Brief and design: `docs/tasks/STAGE-5.md`, `docs/tasks/STAGE-5-DESIGN.md`.

> **Jev evaluation: blocked — no live access** (OQ-1). Everything below runs on a deterministic fake client and on
> replay. The fake is not a model; no statement here is about Jev's quality.

## When Mode A decides — first look

A Stage 3 candidate describes a whole episode (maximum score, end, recovery). Mode A decides at
`as_of = detected_at` from `detection.as_of_view` (first flagged window only, `score_at_detection`,
`methods_at_detection`); Stage 4 runs unchanged on the as-of window `[window_start, detected_at)`; concurrency counts
only candidates already detected (`concurrent_at`). Decided: `main` candidates that were promoted or later recovered.
Orchestration: `policy.mode_a.run`.

## JevState v2 (`jev.state`)

18 closed-enum fields, bucket edges in `jev.buckets`; no dimension values, numbers, timestamps, ids or ground truth.
Hard budget 2,048 bytes of canonical JSON (DEV: 421–484 bytes); above it the state is rejected, never truncated.

| Field | Values |
|---|---|
| `metric_family`, `direction`, `cadence`, `scope_dims` | from the candidate (dimension names only) |
| `relative_change` | drops: none · slight · moderate · severe · collapse; rises (rates and counts): slight · moderate · severe · extreme |
| `strength` | weak (z < 4) · moderate (< 8) · strong — z of the window that opened the episode |
| `detection_rule`, `sample_size` | persistence · cusum · both; small (< 300) · medium (< 3,000) · large |
| `change_type`, `locus_kind`, `locus_dims`, `locus_share` | Stage 4 label; whole_scope · sub_cohort · new_cohort · none; added dimension names; minor · partial · most · nearly_all |
| `cohorts_moved`, `controls`, `related_metrics` | none · one · few · many; present · absent; agree · disagree · mixed |
| `impact` | **per hour** of the window: negligible (< 3.8) · small (< 19) · medium (< 44) · large (v2, ADR-040) |
| `concurrent_alerts`, `calendar_context` | none · one · several; not_provided (no typed source) |

Missing evidence is `not_provided`. The builder also returns **provenance** (field → evidence ids), which Jev never
sees and the audit record keeps. `state_hash = sha256(schema_version + "\n" + canonical_json)`.

## Question set v1 (`jev.questions`, resolves OQ-5)

| Id | Type | Role |
|---|---|---|
| `is_incident` | noul | primary gate; target: ground-truth `is_incident` (incident and watch records) |
| `severity` | choice low · medium · high · critical | INCIDENT needs ≥ medium |
| `category` | choice, cause vocabulary v1 (13) | audit only; never changes the route |
| `needs_human` | noul | ≥ 0.6 → DIGEST; target proxy: watch records |

`recovery_likelihood`, per-cause plausibility and novelty are deferred.

## Client, replay, verifier

- `JevClient` port: `FakeJevClient` (deterministic from the request hash, injects malformed and failing responses),
  `ReplayJevClient` (recorded artifacts; a miss is an error), `HttpJevClient` (the only network I/O; returns
  `not_configured` until provider access exists). `BudgetedClient` refuses before any call when the budget is spent
  or the request is oversized. No retries anywhere.
- Replay artifacts: JSON lines keyed by request hash (gitignored `data/jev_replay/`), secrets redacted.
- Verifier (`jev.verifier`): schema, question ids (missing / duplicate / unknown), options, ranges, choice sums
  (± 0.01), provenance, state hash, schema and question-set versions, pinned model, freshness, response size — each
  failure a reason code; nothing repaired or inferred.

## Policy

`policy_v1` (`policy.rules`), first matching row wins:

| # | Condition | Route |
|---|---|---|
| 0 | not verified / client error / budget exhausted | DIGEST |
| 1 | `is_incident` < 0.30 | IGNORE |
| 2 | … but strength strong and impact medium or large (safety) | DIGEST |
| 3 | 0.30 ≤ `is_incident` < 0.70 | DIGEST |
| 4 | `needs_human` ≥ 0.60 | DIGEST |
| 5 | Stage 4 says healthy composition and P(severity ≥ high) < 0.5 | DIGEST |
| 6 | no locus or insufficient data, unless critical with `is_incident` ≥ 0.85 | DIGEST |
| 7 | severity low | DIGEST |
| 8 | otherwise | INCIDENT |

Thresholds were fixed at Gate 0 and are **not tuned** (no live Jev answers). `baseline_v2` (`policy.baseline`, no
Jev, benchmark control only — not the fallback): INCIDENT if strength strong, change rate-like and impact provided;
IGNORE for weak composition; otherwise DIGEST — tuned on DEV seeds 1–10 (`scripts/tune_stage5_dev.py`).

## Audit

`policy.decide` returns one `DecisionRecord` per candidate, failures included (state, hashes, versions, requested and
returned model, raw answers, verifier reasons, policy version, route and rule, evidence ids and provenance, explicit
timestamps, actor, calls). `policy.audit` appends them to the ClickHouse table `jev_decisions` (MergeTree, partition
by run, never updated; a re-decision is a new row).

## Evaluation (ADR-039 D-9)

Stage 3 matching unchanged; one status per candidate (incident > watch > suppress > unmatched); candidate-level
routes, not pages. Code: `evaluation/decisions.py`; runner `scripts/eval_decisions_dev.py` (DEV only, refuses
HELDOUT). Tuning seeds 1–10, validation seeds 11–20; validation is clean only for Stage 5 thresholds.

### Results on DEV seeds (20 seeds × realism v1/v2, scale 1.0, randomized calendar, first look)

**No-Jev control** (candidate-level routes; validation = seeds 11–20, never used for tuning):

| Control | Seeds | INCIDENT / day | DIGEST / day | IGNORE / day | Incident recall at INCIDENT | Route accuracy (matched) | Unmatched → INCIDENT / day | Suppress → INCIDENT / day |
|---|---|---|---|---|---|---|---|---|
| `baseline_v1` (not tuned) | validation, v1 / v2 | 0.22 / 0.24 | 1.86 / 1.72 | 0.06 / 0.06 | 17 / 18 % | 11 / 12 % | 0.06 / 0.06 | 0.03 / 0.03 |
| `baseline_v2` (tuned 1–10) | tuning, v1 / v2 | 0.37 / 0.36 | 1.75 / 1.66 | 0.09 / 0.07 | 21 / 21 % | 20 / 19 % | 0.07 / 0.06 | 0 / 0 |
| **`baseline_v2`** | **validation, v1 / v2** | **0.30 / 0.30** | 1.79 / 1.67 | 0.06 / 0.06 | **17 / 17 %** | 16 / 16 % | 0.07 / 0.07 | 0 / 0 |

The tuning gain in recall did not carry over to validation (21 % → 17 %, the same as v1); v2 removed suppress records
from INCIDENT and raised route accuracy. On validation, v2 routes 37 of 47 card-testing candidates to INCIDENT and
almost no PSP, payment-method or country degradation (1 of 95).
A control this conservative is easy to beat on recall; Jev must also beat its 0.07 unmatched INCIDENT routes per day.

**Stage 4 at first look** (the real input): top-1 exact locus 41–42 %, equivalence-aware 55–56 %, rate-vs-mix label
57–58 %, `insufficient_data` 14–15 % (full window: 44–45 / 61 / 82–83 %).

**Pipeline** (fake client, technical only): 2,116 decisions = 2,116 audit records, 0 live calls; states 421–484
bytes; 92 % verified, every unverified answer (the fake's injected defects and errors) routed to DIGEST with its
reason; Mode A ≈ 16 s per world.

## Known limitations

| Limitation | Evidence | Where it is addressed |
|---|---|---|
| Jev not evaluated | no provider access (OQ-1) | live run, `policy_v1` tuning on seeds 1–10, request-removal ablations — when access and a budget exist |
| First-look Stage 4 labels are weaker | rate-vs-mix label correct 57–58 % at first look vs 82–83 % on the full window | Stage 6 re-evaluation as an episode evolves |
| No-Jev control catches few incidents at INCIDENT | see results; the non-incident budget of 0.10 per day binds | it is a control; Jev must beat it |
| `needs_human` has only a weak proxy target | watch records | revisit with live answers |
| No calendar context | no typed source | a typed calendar interface, later |
