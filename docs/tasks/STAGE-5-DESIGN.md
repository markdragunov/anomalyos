# Stage 5 — design note (Gate 0)

Status: **accepted** (owner OK on recommendations 1–6, 2026-10-03; ADR-039). Decisions marked **D-n** are recorded
in ADR-039. Jev access: **none** (owner, 2026-10-03) — everything below works on the fake client and replay; OQ-1 stays
open and every provider-specific item is marked *pending access*.

## D-0. When Mode A decides (new finding — decide first)

A Stage 3 `AnomalyCandidate` describes the **whole episode**: `score` is the maximum z over the episode,
`window_end` is the end of the last flagged window, `method` collects every rule that fired, `status = recovered`
and `recovered_at` are set when the episode closes, and `parent_id` uses whole-episode overlap. Stage 4 analyses the
window `[window_start, window_end)`. Only `window_start`, `detected_at`, `observed`, `expected`, `delta`,
`relative_delta` and `sample_size` (first flagged window) are known at `detected_at`.

A Mode A decision taken at `detected_at` from the candidate as stored would therefore use information from the future
(the rule in `.claude/rules/clickhouse.md`, and the first-look principle of ADR-034 D-6).

| Option | What it means | Cost |
|---|---|---|
| **A — decide at first look** (`as_of = detected_at`) | State built only from as-of-safe fields; Stage 4 run on the as-of window `[window_start, detected_at)` (the Stage 4 API and config unchanged, only the candidate window passed in); linked candidates restricted to those with `detected_at ≤ as_of`. Needs two **additive** Stage 3 fields, `score_at_detection` and `methods_at_detection` (no change to what or when Stage 3 detects). | One small Stage 3 change (ADR); Stage 4 sees less data than in its own evaluation, so its as-of quality must be re-measured and reported as the real input at Gate 1. |
| B — decide at episode close (`as_of = window_end` or `recovered_at`) | Use candidates and Stage 4 analyses as they are. | Not Mode A: a page after the episode is over; recovered episodes trivially look benign; route accuracy would be inflated. |

**Recommendation: A.** It is the only option that is honest for paging. Re-evaluation as an episode evolves
(`DETECTED → RECOVERING`, etc.) is the incident lifecycle (Stage 6), not Stage 5. Under A, *observed recovery* is
never known at first look, so it is not a v1 state field (brief 0.3 allows this).

Which candidates are decided: `system = "main"`, status `promoted` (and `recovered`, viewed as of `detected_at`);
`suppressed` candidates and the static comparison system are not; the sweep is off (ADR-038).

## D-1. Input contract and `JevState` v1

Builder input: the as-of view of the `AnomalyCandidate`, its `CohortAnalysis` and `EvidenceBundle` (as-of window),
and as-of linked candidates. The `EvidenceBundle` is the canonical source for every Stage 4 field; the candidate for
Stage 3 fields; no field is read from two places. Missing inputs become `not_provided`; contradictions are not
resolved by the builder (they are visible to the policy, D-7).

| Field | Values (closed) | Source | Evidence for provenance |
|---|---|---|---|
| `metric_family` | approval · conversion · renewal · dunning · refunds · duplicates · volume · fraud · cancellations · ingestion | candidate `metric` (code table) | candidate first-window evidence id |
| `direction` | down · up | candidate | same |
| `cadence` | intraday · daily | candidate `grain` | same |
| `scope_dims` | ≤ 3 dimension **names** (no values), or `global` | candidate `scope` | same |
| `relative_change` | rates: none < 2 % · slight < 5 % · moderate < 15 % · severe < 50 % · collapse ≥ 50 %; counts up: slight < 50 % · moderate < 100 % · severe < 300 % · extreme ≥ 300 % | `relative_delta` | same |
| `strength` | weak (z < 4) · moderate (4 ≤ z < 8) · strong (z ≥ 8) | `score_at_detection` (D-0) | same |
| `detection_rule` | persistence · cusum · both | `methods_at_detection` | same |
| `sample_size` | small < 300 · medium < 3,000 · large ≥ 3,000 | `sample_size` | same |
| `change_type` | rate_change · mix_shift · mixed · new_cohort · volume_only · insufficient_data | bundle `label` | bundle id |
| `locus_kind` | whole_scope · sub_cohort · new_cohort · none | bundle `locus` | top-cohort evidence id |
| `locus_dims` | ≤ 3 dimension names added to the scope | bundle `locus` | top-cohort evidence id |
| `locus_share` | minor < 0.3 · partial < 0.6 · most < 0.9 · nearly_all ≥ 0.9 · not_provided | coverage of the locus cohort | top-cohort evidence id |
| `cohorts_moved` | none · one · few (2–5) · many (> 5) | bundle `discoveries` | bundle id |
| `controls` | present · absent | bundle `controls` | control evidence ids |
| `related_metrics` | agree · disagree · mixed · not_provided (moved = same direction with \|z\| ≥ 2) | bundle `related_metrics` | their evidence ids |
| `impact` | rates down, lost successes: negligible < 10 · small < 100 · medium < 1,000 · large ≥ 1,000; up / counts: same edges on excess events; not_provided | bundle `impact` (`estimated`) | impact / locus evidence id |
| `concurrent_alerts` | none · one · several | as-of linked candidates (other metrics or scopes, `detected_at ≤ as_of`, overlapping `[window_start, as_of)`) | their first-window evidence ids |
| `calendar_context` | not_provided (D-11) | — | — |

Deliberately excluded: dimension values (`psp_alpha`, `BR`) and metric names (identifiers, no decision value beyond
the family), all exact numbers, timestamps, onset (constant at first look under D-0), observed recovery (unknown at
first look), evidence ids, `run_id`, `scenario_id`, seed, anything from ground truth. All values are closed enums, so
the state is bounded by construction.

**Size.** Hard budget **2,048 bytes** of canonical JSON (estimate ≈ 700 bytes); enforced before any client call.
Above the budget the state is rejected (`state_too_large` → `DIGEST`), never truncated — with closed enums and
≤ 3-item lists this can only be a bug. Tokens: *pending access*. Phase 1 reports the size distribution on DEV.

**Provenance.** The builder returns `(JevState, EvidenceProvenance)`; provenance maps each field to its evidence ids
(table above). Jev never sees it; the decision record stores it with the state hash and schema version.

## D-2. Time, freshness and canonical serialization

`DecisionContext(as_of = detected_at, evaluated_at, observation_window = [window_start, as_of), freshness_s)`, all
explicit inputs; nothing reads the clock. In replay and fake modes `evaluated_at = as_of`. Freshness: a response
counts only if recorded for the same request hash and `response_at − evaluated_at ≤ freshness_s` (default 900 s;
provider latency *pending access*). Canonical JSON: keys sorted, `,`/`:` separators, enums as lower-case strings,
explicit `null`, no floats in the state, UTF-8; `state_hash = sha256(schema_version + "\n" + canonical_json)`.

## D-3. Question set v1 (OQ-5)

| ID | Type | Wording (draft) | Options / range | Policy role | Evaluation target |
|---|---|---|---|---|---|
| `is_incident` | noul | "Is this change a real operational problem rather than normal variation in traffic or behaviour?" | probability | primary gate | ground-truth `is_incident` (incident **and** watch records true) |
| `severity` | choice | "If this is a real problem, how severe is it for the business?" | low · medium · high · critical (probability per option + confidence) | INCIDENT needs ≥ medium | ground-truth `severity` on matched incident candidates |
| `category` | choice | "Which cause best fits this change?" | cause vocabulary v1 (13 options) | audit only; never gates | `true_cause` top-1 / top-3 |
| `needs_human` | noul | "Is the evidence ambiguous enough that a person should review it before anyone is paged?" | probability | ≥ threshold → DIGEST | proxy: watch records (stated as a weak proxy) |

Wording rules: one judgment per question, no numbers or dates in the wording, no negations, the same terms as the
state values, no reference to other questions. Recovery: `recovery_likelihood` deferred (no target defined);
per-cause plausibility and novelty not in v1. Scenario coverage for the semantics review and the fake fixtures: PSP
degradation, gradual drift, small cohort / benign shock, recovery after degradation, harmless seasonality / campaign.

Ablations fixed now: **answer removal** (offline, no live cost) — policy without `needs_human`, without `severity`
(INCIDENT on `is_incident` alone), with `category` shown to have no route effect. **Request removal** (asking fewer
questions) needs live calls → deferred until access and budget exist; never claimed from answer removal.

## D-4. `JevClient` port and transport (ADR-024)

`JevRequest(question_set_version, questions, state, state_schema_version, state_hash, requested_model, metadata)` →
`JevResponse(answers, returned_model, provider_request_id, raw, request_at, response_at)` or
`JevError(kind ∈ {timeout, transport, provider, replay_miss, budget_exhausted, not_configured}, detail)`.
`JevClient` is a protocol with three implementations: `FakeJevClient`, `ReplayJevClient`, `HttpJevClient`.
`HttpJevClient` lives alone in `jev/transport.py` (stdlib `urllib`, explicit timeout, no retry, bounded sizes, key from
`load_settings`, never logged); until the provider's wire format is known it returns `not_configured`
(*pending access*). Model pinning: the verifier compares `returned_model` with the configured pinned version; missing
or different → `unverified` (`model_mismatch`).

## D-5. Replay and execution modes

`fake` (unit tests and the Gate 1 pipeline run), `replay` (default in tests, CI, evaluation), `record` (live, owner
approval, budget guard; unavailable without access). Replay artifacts: JSON lines under `data/jev_replay/`
(gitignored), keyed by request hash, with every field the brief lists (raw response, hashes, versions, transport
version, provider request id, timestamps, artifact version); secrets redacted before writing. A replay miss is an
error (`replay_miss` → `DIGEST`), never a silent fake. Small committed fixtures live under `tests/`.

**The fake is not a model.** It answers deterministically from the state hash (pseudo-random probabilities derived
from SHA-256 of the request hash; no global randomness, no dependency on the simulator), plus a fixed share of malformed and failing responses so every verifier and fallback path is
exercised. Its route results are pipeline diagnostics only.

## D-6. Verifier

Pure function `(request, response | error, provenance, context, config) → Verified | Unverified(reasons)`. Reason codes:
`schema`, `missing_question`, `duplicate_question`, `unknown_question`, `invalid_option`, `out_of_range`,
`inconsistent_choice` (option probabilities not summing to 1 ± 0.01, or confidence outside [0, 1]),
`cardinality`, `missing_provenance`, `state_hash_mismatch`, `schema_version_mismatch`, `question_set_mismatch`,
`model_mismatch`, `stale_response`, `oversized_response`, plus the client error kinds. No cross-question identities
are checked (ADR-018). Nothing is repaired or inferred.

## D-7. Policy (`policy_v1`, values fixed now, tuned only at Gate 1 on the tuning seeds)

Inputs: verified answers, the state, config. Severity level = arg-max option; P(severity ≥ high) = sum of the
high and critical probabilities.

| # | Condition (first match wins) | Route |
|---|---|---|
| 0 | not verified / any client error / budget exhausted | DIGEST (failure fallback, ADR-028) |
| 1 | `is_incident` < 0.30 and not (strength = strong and impact ∈ {medium, large}) | IGNORE |
| 2 | `is_incident` < 0.30 (safety: strong signal with real impact is never ignored) | DIGEST |
| 3 | 0.30 ≤ `is_incident` < 0.70 (uncertainty band) | DIGEST |
| 4 | `needs_human` ≥ 0.60 | DIGEST |
| 5 | `change_type` ∈ {volume_only, mix_shift} (Stage 4 says healthy composition) and P(severity ≥ high) < 0.50 | DIGEST |
| 6 | `locus_kind` = none or `change_type` = insufficient_data, unless severity = critical and `is_incident` ≥ 0.85 | DIGEST |
| 7 | severity level = low | DIGEST |
| 8 | otherwise (`is_incident` ≥ 0.70, severity ≥ medium) | INCIDENT |

`category` (including `unknown`) never changes the route. **No-Jev baseline** (`baseline_v1`, benchmark control
only): INCIDENT if strength ≥ moderate, `change_type` ∈ {rate_change, new_cohort, mixed}, `impact` ∈ {medium, large}
and `locus_share` ≥ partial; IGNORE if `change_type` ∈ {mix_shift, volume_only} and strength = weak; otherwise DIGEST.
Both are pure functions of their inputs and version.

## D-8. Assessment, audit and storage

`Assessment` assembled in code (incident probability, severity distribution, cause distribution, `needs_human`,
verifier status). One decision record per decided candidate, with every brief 0.8 field; actor `mode_a_pipeline`;
budgets consumed (calls, bytes). Storage boundary:

1. **Runtime audit** — ClickHouse append-only table `jev_decisions` in the run database (one row per decision, never
   updated; a re-decision is a new row), consistent with ADR-028; DDL in an ADR.
2. **Replay artifacts** — files (D-5), classified as replay / evaluation artifacts, not runtime audit.
3. **Evaluation** — `evaluation/` only, joining decisions to ground truth by candidate id in `reports/`; nothing
   evaluation-only crosses into `jev` or `policy` (INV-015 test extended to both packages).

## D-9. Evaluation protocol (fixed before results)

Stage 3 matching unchanged. One status per candidate, precedence **incident > watch > suppress > unmatched**; a
candidate on a declared unchanged metric is `unmatched` (as in Stage 3, a false alarm). Several candidates on one
incident: recall at INCIDENT counts the incident once if any of them is routed INCIDENT; "INCIDENT routes per matched
incident" reports the multiplicity. Metrics, comparison and names as in the brief (`realism_v1/v2`,
`question_set_v1`). **Split:** tuning seeds 1–10, validation seeds 11–20, both realisms; validation is clean only for
Stage 5 thresholds (brief). Stage 4 quality on the as-of window (D-0) is reported alongside, as the real input.
Without access, Gate 1 reports: baseline on validation seeds, the fake pipeline (route mix, failure handling, audit
completeness, sizes), and `Jev evaluation: blocked — no live access`.

## D-10. Budget guard

A wrapper in front of any client counts calls and request bytes against `JevBudget(max_calls, max_cost, max_cost_per
candidate, max_request_bytes, max_response_bytes, rate_limit, timeout)`; exhausted → no call, `budget_exhausted`,
DIGEST, no retry. With no access the `record` budget is 0 (record mode refuses to start).

## D-11. Calendar context

No typed calendar, campaign or deploy source exists in Stage 3/4 outputs → `not_provided`. Nothing from the simulator
calendar or scenario metadata is used.

## D-12. Scope

Mode A only. Mode B questions → Stage 7 (ADR-019 option 3); grouping and lifecycle → Stage 6; no generated text.

## Phase 1 sketch (after approval)

`src/pulseos/jev/` (`state.py`, `buckets.py`, `questions.py`, `client.py`, `fake.py`, `replay.py`, `transport.py`,
`verifier.py`, `assessment.py`) and `src/pulseos/policy/` (`config.py`, `rules.py`, `baseline.py`, `records.py`,
`audit.py`); Stage 3 additive fields (D-0); `jev` and `policy` removed from the stage guard; tests per the brief;
`scripts/eval_decisions_dev.py`.

## Open questions for the owner

1. **D-0:** decide at first look (A, recommended) with the two additive Stage 3 fields and Stage 4 on the as-of window,
   or at episode close (B)?
2. **D-1:** state fields and bucket edges as proposed? Dimension values excluded (recommended) or included?
3. **D-3:** question wording and `needs_human`'s weak proxy target acceptable?
4. **D-7:** decision table and initial thresholds as proposed (tuned later on seeds 1–10 only)?
5. **D-8:** runtime audit in a ClickHouse append-only table `jev_decisions` (recommended), or files until Stage 6?
6. **D-9:** split 1–10 tuning / 11–20 validation?
