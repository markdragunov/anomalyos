# Stage 6 — design note (Gate 0)

Status: **proposed**, awaiting owner approval. No product code exists yet. Decisions marked **D-n** become ADR-041
(and, if needed, ADR-042 … ADR-045) after approval. Jev is still blocked (OQ-1): nothing here depends on Jev quality.

## D-0. Event stream and time

The engine is a **pure fold** over a time-ordered event stream: `state' = apply(state, event)`, events sorted by
`(time, kind order, id)`. Same events and versions ⇒ same incident history; no clock reads.

| Event | Time | Payload |
|---|---|---|
| `candidate_detected` | `detected_at` | first-look candidate, its Stage 4 analysis, its Stage 5 decision |
| `candidate_checkpoint` | checkpoint time (D-5) | as-of candidate, Stage 4 on `[window_start, t)`, a new Stage 5 decision |
| `candidate_recovered` | `recovered_at` | candidate id |
| `human_action` | given | actor, command, incident id, reason |

The engine sees episodes at detection, at recovery and at a bounded number of checkpoints (D-5) — not at every window
close. Cost bound: ≤ 4 decisions per candidate.

## D-1. Route handling

- `IGNORE`: no engine object; the Stage 5 audit record is the trace.
- `DIGEST`: a **digest item** per candidate, grouped into digest groups by the same correlation rules (D-2). A digest
  candidate that correlates with an open incident is linked to it as supporting evidence (it never pages on its own).
- `INCIDENT`: creates an incident or is linked to one (D-2).
- **Upgrade:** if a checkpoint decision routes `INCIDENT` for a candidate in a digest group, the group becomes an
  incident (its candidates linked). **No automatic downgrade:** a later `IGNORE` / `DIGEST` only annotates.

## D-2. Correlation rules (refines the ADR-028 working rule)

A candidate links to an open incident (or digest group) only if **all** hold:

1. **Time:** its interval overlaps the incident's interval (both as known at the event time) or the gap is ≤ `G`
   (initial 1 h; tuned on seeds 1–10 over {0, 1 h, 3 h, 6 h}).
2. **Cohort:** loci are **nested** — compare Stage 4 loci (the candidate's scope when the locus is missing): one
   locus's (dimension, value) pairs are a subset of the other's, along the approved chains
   `psp ⊂ psp×country ⊂ psp×country×platform`, `psp ⊂ psp×card_brand`, `country ⊂ psp×country`,
   `country ⊂ country×payment_method`, `platform ⊂ platform×app_version`, and any locus ⊂ itself.
   **Global loci are ambiguous:** a candidate with a global locus links only if exactly one open incident satisfies
   rules 1 and 3; with two or more it stays separate and is marked `related_to` each of them.
3. **Metric families** share a group (engine-owned table, no import from evaluation):

| Group | Metrics |
|---|---|
| payments | `authorization_rate`, `checkout_conversion_rate`, `attempt_volume` (down) |
| fraud | `fraud_flag_rate`, `attempt_volume` (up), `authorization_rate` |
| subscriptions | `renewal_success_rate`, `dunning_recovery_rate` |
| refunds and duplicates | `refund_count`, `duplicate_charge_rate` |
| cancellations | `subscription_cancellation_rate` |
| ingestion | `late_arrival_share` (links to any group: late data moves every first-look metric of its cohort) |

Otherwise the candidate starts a new incident; if it satisfies 1 and 3 but not 2, it is recorded as `related_to`.
**Tie-break:** the oldest matching incident `(created_at, incident_id)`. **No automatic merge of two existing incidents
and no silent split:** both are human commands. Every link row stores why (time relation, cohort relation, group).
Jev never creates a relationship.

**Campaign + outage negative case:** the campaign's candidates are `attempt_volume` up with a country locus; the
outage's are `authorization_rate` down with a payment-method locus. Their metric groups **do** share one (fraud
contains both `attempt_volume` up and `authorization_rate`), so rule 3 alone would not separate them; **rule 2 does** —
a country locus and a payment-method locus are not nested. A global approval candidate overlapping both is ambiguous
by rule 2 and stays separate. A unit test pins this for both variants (iDEAL and SEPA).

## D-3. Incident object

| Field | Source | Label |
|---|---|---|
| `incident_id` | stable hash of the first candidate id | — |
| `title` | code template: family, direction, locus (ADR-027) | — |
| `status` | lifecycle (D-4) | — |
| `started_at` / `detected_at` / `last_updated_at` | min `window_start` / first `detected_at` / last event time | OBSERVED |
| `affected_dimensions` | most specific locus among linked candidates | INFERRED (Stage 4) |
| `category`, `hypotheses`, `severity`, `confidence` | latest **verified** decision (top cause, top-3 causes, severity level, `is_incident`) | INFERRED; `not_evaluated` while Jev is blocked |
| `estimated_impact` | D-6 | ESTIMATED / OBSERVED per value |
| `evidence_ids`, `linked_anomalies` | union over linked candidates and their decisions | — |
| `investigation_status` | `not_started` (Mode B is Stage 7) | — |
| `route_source`, `policy_version` | the decision that created / upgraded it | — |

## D-4. Lifecycle (ADR-028)

- **System:** creation → `DETECTED`. All linked candidates recovered and none detected for `H` (hysteresis; initial
  1 h, tuned over {0, 1 h, 3 h}) → `RECOVERING`. In `RECOVERING` a new linked candidate → `INVESTIGATING` (signal
  returned; no flapping close). `DETECTED → INVESTIGATING` (Mode B started) is not produced until Stage 7.
- **Human commands** (typed, actor and reason required): `acknowledge`, `escalate`, `resolve`, `dismiss`,
  `merge(a into b)` (b absorbs a's links; a is `DISMISSED` with reason "merged into b", by the human).
- **Escalation rule:** none automatic in Stage 6 — the only candidate rule (critical severity with high confidence)
  needs evaluated Jev answers; ADR-028 also allows escalation only from `ACKNOWLEDGED` / `INVESTIGATING`.
- Only a `human` actor can produce `RESOLVED` / `DISMISSED`; the transition function rejects anything else. Every
  change is a new row with actor (`system` / `policy` / `human`) and reason.

## D-5. Re-evaluation (recommend option b)

Checkpoints per candidate: `detected_at + {6 h, 24 h}` while the episode is open, and `recovered_at`; at most 3,
dropped when the incident is already terminal. Each checkpoint re-runs Stage 4 on `[window_start, t)` and a Stage 5
decision (a new decision record), and may upgrade `DIGEST → INCIDENT` and update severity, category, confidence and
affected dimensions — never close or downgrade. Cost: ≤ 4 decisions per candidate (≈ 8,500 for 40 DEV worlds; Jev calls
when access exists, counted in its budget). Gate 1 reports Stage 4 label quality at each checkpoint vs first look.

## D-6. Impact

- **OBSERVED:** attempts and captured revenue (`revenue_collected_minor`, per currency) in the incident's most specific
  locus over its interval.
- **ESTIMATED:** lost successful payments = Stage 4 estimate of the anchor candidate (the most specific approval /
  conversion candidate, at its latest checkpoint), extended to the incident interval; lost revenue per currency =
  baseline (Stage 3 lagged same-slot rule on `revenue_collected_minor` in the locus) − observed, with an interval from
  the reference days' spread. One anchor per incident, so overlapping candidates are not double counted.
- Queries only through `metrics.compute` (INV-003), at checkpoints. Compared with ground-truth `true_impact` and
  `expected_impact` in evaluation only.

## D-7. Storage and package

Package `src/pulseos/incidents/` (remove `incidents` from the stage guard; `incident` stays forbidden). Append-only
ClickHouse tables (ADR-028), DDL in the ADR:

| Table | Row |
|---|---|
| `incident_events` | one per change: incident id, seq, time, status, snapshot of the incident fields, actor, reason |
| `incident_links` | incident id, candidate id, linked at, rule evidence, `link` / `related_to` |
| `digest_items` | candidate id, digest group id, time, decision id, promoted-to incident id (nullable) |

Current state = latest `incident_events` row per incident (`argMax` by seq), as a view.

## D-8. Burst ranking — defer

About 2 decided candidates per world-day and no Jev call budget pressure yet; the burst ranker (spec 04: interface
only) is recorded as deferred until Jev access and a budget exist.

## D-9. Evaluation protocol (fixed before results)

DEV seeds only; HELDOUT untouched. Each linked candidate keeps its Stage 3 matching status and record (Stage 5
precedence). An engine incident's **main record** = the incident record matched by most of its candidates (ties:
earliest start).

- **Coverage:** ground-truth incident records (with `oracle_detectable_at`) that have ≥ 1 candidate linked to some
  engine incident; **duplicates:** engine incidents sharing a main record, minus one, per covered record;
  **purity:** share of an engine incident's candidates matched to its main record; **wrong merges:** engine incidents
  holding candidates of two records that list each other in `unrelated_to` (the campaign + outage pairs reported
  separately and required to be 0).
- **Volume:** engine incidents created per day, false incidents per day (main status unmatched / suppress), digest
  groups per day.
- **Timeliness and recovery:** creation − `max(start, oracle_detectable_at)`; `RECOVERING` reached within 6 h of the
  true recovery (`expected_recovery`, else `end`); count of system transitions to a terminal state (must be 0).
- **Impact:** median relative error and interval coverage — lost successful payments vs `expected_impact`, lost
  revenue per currency vs `true_impact.lost_revenue_minor`.
- **Route sources**, reported separately: (i) route-agnostic (every decided candidate treated as `INCIDENT`), (ii)
  `baseline_v2` routes with re-evaluation (the control); the fake client only in technical tests.
- **Tuning** of `G` and `H` on seeds 1–10, rules fixed now: (1) zero campaign + outage merges; (2) wrong merges ≤ 5 %
  of engine incidents; (3) then fewest duplicates per covered record; ties: smaller `G`, then smaller `H`. Reported on
  seeds 11–20.

## D-10. Scope

As the brief: no Mode B, API, UI, notifications, generated text, Jev live calls, HELDOUT, new dependencies, writes to
billing state.

## Open questions for the owner

1. **D-2:** correlation on Stage 4 loci with the chain list and the group table as proposed; global loci linked only
   when unambiguous?
2. **D-2 / D-4:** no automatic merge of existing incidents (merge and split are human commands)?
3. **D-4:** no automatic escalation in Stage 6?
4. **D-5:** re-evaluation at `+6 h`, `+24 h` and recovery (≤ 3 per candidate), upgrades only?
5. **D-6:** impact anchored on one candidate per incident; lost revenue from `revenue_collected_minor` against the
   Stage 3 baseline?
6. **D-7:** three append-only tables and package `incidents`?
7. **D-8:** defer burst ranking?
8. **D-9:** evaluation definitions and the `G` / `H` tuning rule as proposed?
