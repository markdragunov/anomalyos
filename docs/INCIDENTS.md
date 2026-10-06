# Incident engine (Stage 6)

Turns Mode A decisions into a coherent, auditable incident system: related candidates are correlated into one
incident, `DIGEST` items are kept, each incident follows the ADR-028 lifecycle as its episodes evolve, and its impact
is estimated. **Code correlates and moves state; humans close.** No Jev-created relationships, no autonomous closure,
no generated text. Code: `src/pulseos/incidents/`. Decisions: ADR-041 (design, Gate 0, with an amendment) and ADR-042
(Gate 1). Brief and design: `docs/tasks/STAGE-6.md`, `docs/tasks/STAGE-6-DESIGN.md`.

> **Jev is still not evaluated** (no provider access, OQ-1). Stage 6 is evaluated route-agnostically (every decided
> candidate treated as `INCIDENT`) and with the no-Jev control `baseline_v2`; the fake client only feeds the decision
> records.

## Event stream (`incidents.pipeline`, `incidents.engine`)

The engine is a pure fold over a time-ordered event stream; the same events and versions give the same rows; no clock.

| Event | Time | Carries |
|---|---|---|
| `detected` | `detected_at` | first-look locus (Stage 4) and decision (Stage 5) |
| `checkpoint` | `detected_at` + 6 h, + 24 h while open, and `recovered_at` (≤ 3) | Stage 4 on `[window_start, t)` and a new decision |
| `recovered`, `recovery_check` | `recovered_at`, `recovered_at` + `H` | — |
| `human` | given | actor, command, reason |
| `impact` | incident's last update | the estimate (D-6) |

A checkpoint keeps the first-look `score_at_detection` (the episode's running maximum is not stored per window).

## Routes

`IGNORE`: nothing beyond the Stage 5 audit record. `DIGEST`: a digest item, grouped by the correlation rules, or
supporting evidence for an open incident it correlates with. `INCIDENT`: creates an incident or joins one. A later
`INCIDENT` decision promotes a digest group to an incident; nothing is downgraded or closed automatically.

## Correlation (`incidents.correlate`, ADR-041 D-2, ADR-042)

A candidate joins an open incident only if **all** hold against one of its members:

1. **Time:** intervals overlap or the gap is ≤ `G` (6 h).
2. **Cohort:** Stage 4 loci are nested along the approved chains `psp ⊂ psp×country ⊂ psp×country×platform`,
   `psp ⊂ psp×card_brand`, `country ⊂ psp×country`, `country ⊂ country×payment_method`, `platform ⊂ platform×app_version`
   (equal loci always). A **global candidate** joins only when exactly one incident qualifies; a **global member**
   never anchors a specific candidate. A checkpoint refines a member's locus only if it still nests with the member it
   joined through.
3. **Metric group:** payments (`authorization_rate`, `checkout_conversion_rate`, `attempt_volume` down) · fraud
   (`fraud_flag_rate`, `attempt_volume` up) · subscriptions · refunds and duplicates · cancellations · ingestion
   (compatible with every group).

Otherwise a new incident, `related_to` the incidents that matched only time and group. Ties: the oldest incident.
Merging and splitting existing incidents are human commands. Every link records why.

## Lifecycle (`incidents.lifecycle`, ADR-028)

System: `DETECTED` on creation; `RECOVERING` when every linked signal has recovered and `H` (0) has passed;
`RECOVERING → INVESTIGATING` when a signal returns within `G`. A later signal opens a new incident. Human commands:
acknowledge, escalate, resolve, dismiss, merge (actor and reason required). Only a `human` actor reaches `RESOLVED`
or `DISMISSED`; the transition function rejects anything else. No automatic escalation in Stage 6. `DETECTED →
INVESTIGATING` (Mode B started) arrives with Stage 7.

## Incident object and impact (`incidents.impact`)

Fields per spec 07 with epistemic labels: `started_at` OBSERVED; `affected_dimensions`, `category`, `severity`,
`confidence`, `hypotheses` INFERRED (and `jev_status = not_evaluated`); `estimated_impact` ESTIMATED / OBSERVED;
`investigation_status = not_started`. Impact uses **one anchor** (the most specific approval / conversion member) over
**its episode**; a new-cohort anchor is measured on its Stage 3 scope:

- lost successful payments: the anchor's Stage 4 estimate extended to its episode (ESTIMATED, with an interval);
- captured revenue per currency in the locus (OBSERVED);
- lost revenue per currency: **not provided** — its pooled median relative error on tuning seeds was 1.01 (> 1.0,
  ADR-042); the code path stays behind `ESTIMATE_LOST_REVENUE = False`.

## Storage (`incidents.storage`)

Append-only ClickHouse tables `incident_events` (one row per change: status, actor, reason, snapshot), `incident_links`
(why each candidate is linked), `digest_items`; view `incidents_current` = the latest row per incident.

## Evaluation (ADR-041 D-9)

Code: `evaluation/incidents.py`; runner `scripts/eval_incidents_dev.py` (`--tune`: seeds 1–10 only; refuses HELDOUT).
Each linked candidate keeps its Stage 3 matching record; an incident's main record is the incident record most of its
candidates match. Coverage, duplicates per covered record, purity, wrong merges (`unrelated_to` pairs; campaign +
outage pairs must be 0), volume, creation delay, `RECOVERING` within 6 h of the true recovery, system closures,
impact error and interval coverage. Tuning: `G` by the ADR-041 rule, `H` by recovery timing (ADR-042).

### Results on DEV seeds (20 seeds × realism v1/v2, scale 1.0, randomized calendar; `G` = 6 h, `H` = 0)

Route-agnostic, validation seeds 11–20 (v1 / v2) — **not a clean validation for correlation** (the ADR-042 merge
mechanisms were found on these seeds; the next clean check is HELDOUT, Stage 9):

| | ADR-041 + amendment | ADR-042 (current) |
|---|---|---|
| Campaign + outage merges | 1 / 1 | **0 / 0** |
| Wrong merges | 4 / 4 % | 3 / 4 % |
| Coverage of incident records | 86 / 83 % | 86 / 83 % |
| Duplicates per covered record | 0.75 / 0.74 | 0.86 / 0.82 |
| Purity | 76 / 72 % | 76 / 74 % |
| Incidents / false incidents per day | 1.44 / 0.42 · 1.37 / 0.43 | 1.51 / 0.42 · 1.44 / 0.44 |
| `RECOVERING` within 6 h | 59 / 60 % | 62 / 61 % |
| Closed by the system | 0 | **0** |
| Lost payments: median error / interval coverage | 0.62 / 22 % · 0.51 / 31 % | 0.57 / 24 % · 0.59 / 27 % |
| Lost revenue: median error | 2.5 / 3.0 | 1.2 / 1.3 → not provided |

Tuning seeds 1–10 (v1 / v2): coverage 83 / 78 %, duplicates 0.99 / 0.98, wrong merges 3 / 4 %, campaign merges 0.
`baseline_v2` routes: 0.18 incidents per day, coverage 19–28 %, campaign merges 0, system closures 0.
Stage 4 label accuracy across checkpoints: 63 % at first look → 78–80 % at + 6 h → 77 % at + 24 h → 63–65 % at
recovery — re-evaluation pays off.

## Known limitations

| Limitation | Evidence | Where it is addressed |
|---|---|---|
| Duplicates | 0.8–1.0 engine incidents per covered record beyond the first | strict chains and Stage 3 scope dimensions in loci — the deferred scope-dimension task |
| Lost revenue not provided | median error 1.0–1.3 | a better revenue baseline (seasonality, amounts per cohort) |
| Lost-payments interval too narrow | covers the truth in 24–42 % | the Stage 4 interval ignores baseline uncertainty |
| Correlation validated on seen seeds | ADR-042 mechanisms found on seeds 11–20 | HELDOUT, Stage 9 |
| Card testing often split in two | approval drop vs fraud signals in different groups | accepted cost of ADR-042 |
| Jev not evaluated | OQ-1 | live run when access and a budget exist |
| No automatic escalation; Mode B transitions absent | ADR-041 D-4 | Stage 7 |
