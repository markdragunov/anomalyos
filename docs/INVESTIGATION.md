# Investigation agent (Stage 7, Mode B)

Investigates every new incident once, at its creation: narrows the evidence, keeps a small set of hypotheses from the
closed cause vocabulary, runs read-only checks chosen to separate them, and returns a cited report. **Code owns the
loop; the chooser only picks among a shortlist; the agent reads, never writes; humans close.** Code:
`src/pulseos/investigation/`, `src/pulseos/explanation/`, `src/pulseos/context/`. Decisions: ADR-049 (design, Gate 0)
and ADR-050 (Gate 1, with its follow-up). Brief and design: `docs/tasks/STAGE-7.md`, `docs/tasks/STAGE-7-DESIGN.md`.

> **Jev is still not evaluated** (no provider access, OQ-1). The Jev chooser runs on the fake client — a pipeline
> check, not a model evaluation. The product numbers below are the deterministic **control** chooser's.

## Scope and time (D-0, D-6)

- **Which incidents:** every incident, once, at creation (`as_of` = the incident's `detected_at`). Digest items are not
  investigated. Evaluation also runs a diagnostic second pass at `+ 6 h` (`control_6h`); only the first pass is product
  behaviour.
- **Incident change:** one engine event `investigation` (actor `system`): `start` moves `DETECTED → INVESTIGATING`
  (reason `investigation_started`); `finish` sets `investigation_status` (`completed`, `handed_off`,
  `budget_exhausted`, `failed`) and adds the report id to the incident's evidence. Correlation does not read it.
  The agent never resolves, dismisses or merges.
- **Time:** tools read only data before `as_of` at first-look visibility (`window_close`). No clock in any decision;
  an injected clock feeds the wall-clock budget and the audit (a step clock in tests and evaluation).

## The loop (`investigation.loop`, D-2)

OBSERVE → NARROW → HYPOTHESES → SHORTLIST → CHOOSE → EXECUTE → UPDATE → STOP / CONTINUE.

1. **Observe:** `get_incident` — the incident as known at `as_of` (members, anchor, Stage 4 locus, change type).
2. **Narrow** (`investigation.narrowing`, D-1): the anchor's pooled cohort tables are cut into chunks of 40, split in
   4, recursively kept by importance (control: summed |contribution|; Jev: a `noul` per chunk) to ≤ 4 leaves × 10
   items, depth ≤ 3. The anchor's Stage 4 top-5 cohorts are added (ADR-050). Narrowing is not a tool call; its items
   are evidence.
3. **Hypotheses** (`investigation.hypotheses`, D-4): a prior from **every member** of the incident (ADR-050) —
   3 for a locus-dimension match or a direct family (`fraud_flag_rate` up → `fraud_attack`, …), 1 for the same
   family, 0 otherwise; `mix_shift` / `volume_only` give `normal_variation` 3. Up to 7 causes plus `normal_variation`
   and `unknown`. Equal priors and equal scores are ordered by **family order** in `PRIOR_TABLE`, never
   alphabetically (ADR-050 follow-up). Score = prior + supports − 2 × contradictions; status `supported` (≥ 2
   supports, no contradiction), `contradicted` (more contradictions than supports, ≥ 1), else `open`.
4. **Check library** (`investigation.checks`): every check carries the outcome each cause predicts.

   | Kind | Tool | Question | Predictions |
   |---|---|---|---|
   | `sibling` | `compare_cohorts` | locus vs. the best-supported cohort that differs in one dimension | the cause of that dimension: `unchanged`; causes of other locus dimensions: `moved` |
   | `channel` | `get_metric_history` | approval on the locus's payment-side dimensions with `channel = checkout` (client dims `platform`, `app_version` dropped) | renewal and dunning failures, normal variation: `unchanged`; PSP, country, method, fraud, pipeline: `moved` |
   | `related` | `get_related_metrics` | a related metric on the locus (`RELATED` table) | per cause, e.g. approval drop → conversion `moved` for payment-side causes |
   | `deploy` | `get_deployments` | a deploy of the cause's service (or the platform's mobile release) within ± 2 h of the incident start, up to `as_of` | `present:<service>` supports; absence never contradicts |
   | `status` | `check_psp_status` | PSP status-page entries posted by `as_of` and active between 6 h before the incident start and `as_of` | `present:<component>` supports; absence never contradicts |

   Metric outcomes come from a pooled z against lagged reference days: `moved` (|z| ≥ 2 in the expected direction),
   `opposite`, `unchanged` (|z| < 1), otherwise `ambiguous`; `insufficient` when a period has < 30 attempts.
5. **Shortlist:** ≤ 5 unrun checks ranked by how many distinct predictions they make for the top-3 hypotheses; at
   least one **disconfirming** check (a `sibling`, `channel` or `related` check with a prediction for the leader) if
   any is left.
6. **Choose:** the control takes the first; the Jev chooser asks a `step` question over a typed state (closed-enum
   buckets, ids, no free text, ≤ 2 KB) and the Stage 5 verifier checks the answer. An unverified answer stops the
   run (`model_failure`) — no fallback to the control within a run. Confidence < 0.5 hands off.
7. **Execute and update:** one tool call; the result supports a matching prediction and contradicts an opposed one;
   contradicting evidence is kept. A step is never run twice.

**Stops** (`STOP_REASONS`): `sufficient_evidence` (leader supported and the runner-up contradicted or ≥ 2 behind),
`strong_contradiction` (every hypothesis with a prior contradicted → conclusion `unknown`), `false_positive`
(`normal_variation` supported → conclusion `normal_variation`), `budget_exhausted`, `tool_failure` (second failure),
`model_failure`, `low_confidence`, `no_checks_left`, `no_disconfirming_check`. Otherwise the conclusion is the leader
if `supported`, else `insufficient_evidence`. Every stop returns the partial structured state; `calculate_impact`
runs at the end if the budget allows.

## Tools (`investigation.tools`, D-3, INV-007)

Ten typed, read-only tools; arguments from closed sets; one evidence object each with a stable id
(`tool`, args, run, `as_of`, config version) and per-field epistemic labels. Reads go only through
`metrics.compute`, `cohorts.fetch`, `incidents.impact`, `pulseos.context` readers (static SQL with bound
parameters) and in-memory Stage 6 snapshots. No model writes SQL; no tool writes anything; an architecture test
forbids mutation names in the registry.

`get_incident` · `get_metric_history` · `breakdown_by_dimension` (top cohorts of an approved combination) ·
`compare_cohorts` · `get_related_metrics` · `calculate_impact` · `read_evidence` · `search_similar_incidents`
(≤ 5 earlier incidents) · `get_deployments` · `check_psp_status` (≤ 10 ids each). A `ToolError` (argument outside its
set, empty cohort, failed read) is recorded and the loop continues once.

## Budgets (D-5, ADR-050, INV-011)

| Budget | Value |
|---|---|
| Tool calls | **9** (chosen on seeds 1–10: the smallest budget within 1 point of uncapped top-3) |
| Jev calls | 40 |
| Evidence items | 60 |
| Wall-clock | 120 s per investigation, 10 s per query |
| Repeated steps | 0 |

Exceeding a budget is a hard stop (`budget_exhausted`), never a retry.

## Report and explanation (`explanation`, D-7, INV-008, INV-013)

`TemplateExplainer` renders claims from typed state only: a claim is a template id plus slots, each slot an
(evidence id, field, epistemic label). Templates hold no literal numbers and never claim causation: "Evidence
consistent with {cause}: …", "Evidence against {cause}: …", "Context consistent with …", "Insufficient evidence for
…", an `estimated` impact, a recommended next check for a human. The **citation validator** runs before a report is
returned: every slot cites evidence in the registry, the field exists, the label matches the field's label. An
invalid report is not shown (`investigation_status = failed`). An LLM explainer can replace the template behind the
same `Explainer` port and validator later.

## Storage (`investigation.storage`, D-8)

Append-only ClickHouse tables, partitioned by `run_id`: `investigation_steps` (one row per step, JSON),
`investigation_evidence` (evidence objects with labels and `as_of`), `investigation_reports` (chooser, stop reason,
conclusion, hypotheses, claims, validity, errors, budgets used). Insert only.

## Side files (`simulation.side_files`, `context.readers`, sim-1.3.0)

The `get_deployments` and `check_psp_status` tools read `deployments` and `psp_status` — simulator side files that
behave like change logs and status pages: sometimes revealing, often noisy (`docs/SIMULATION.md` §14). Honest entries
exist only for fault effects the scenario author marked; routine deploys, weekly maintenance, minor degradations and
near-miss decoys are always there. Ground truth lists the honest ids (`side_signals`) for evaluation only.

## Evaluation (ADR-049 D-9)

Code: `evaluation/investigation.py`; runner `scripts/eval_investigation_dev.py` (refuses HELDOUT). Systems: `prior`
(the member-wide prior ranking, no checks), `control`, `control_no_side` (side-file checks removed), `fake` (Jev
chooser on the fake client — a pipeline check), `control_6h` (diagnostic second pass). Incidents are route-agnostic
(every decided candidate treated as `INCIDENT`); a `baseline_v2` block uses the no-Jev control routes. Hypotheses are
scored against the incident's main record (the Stage 6 rule): truth at top-1 / top-3; false-positive incidents should
conclude `normal_variation`, `unknown` or `insufficient_evidence`.

### Results on DEV seeds (20 seeds × realism v1/v2, scale 1.0, randomized calendar; `investigation_v4`)

Validation seeds 11–20 (v1 / v2) — **not a clean validation** (the ADR-050 changes were found on these seeds; the
next clean check is HELDOUT, Stage 9):

| | prior | control | control, no side files | fake | control at + 6 h |
|---|---|---|---|---|---|
| Truth top-1 | 56 / 56 % | 55 / 55 % | 54 / 55 % | 54 / 56 % | 55 / 55 % |
| Truth top-3 | 86 / 86 % | **88 / 87 %** | 89 / 87 % | 83 / 82 % | 86 / 88 % |
| False-positive incidents → not a cause | 11 / 10 % | **78 / 72 %** | 84 / 76 % | 95 / 98 % | 83 / 84 % |
| Leader keeps its contradiction (truth not leader) | — | 20 / 23 % | 23 / 26 % | 1 / 2 % | 27 / 28 % |
| Narrowed set hits the locus (Stage 4 top-5) | — | 39 / 38 % (36 / 35 %) | same | 36 / 37 % | 41 / 38 % |
| Cites an honest side signal | — | 17 / 16 % | 0 % | 4 / 3 % | 12 / 13 % |
| A decoy supports the leader | — | 2 / 2 % | 0 % | 1 / 1 % | 3 / 3 % |
| Valid reports · disconfirming check · repeats · over budget | — | 100 % · 100 % · 0 · 0 | same | 100 % · 90–91 % · 0 · 0 | 100 % · 100 % · 0 · 0 |

Tuning seeds 1–10 (v1 / v2): control 56 / 54 % top-1, 85 / 87 % top-3; prior 50 / 48 %, 81 / 82 %. With
`baseline_v2` incidents (44 per realism): control 68 % top-1, 80 % top-3, every false-positive incident concluded
not a cause. Cost (control): median 2 steps, 5 tool calls, 17 evidence items; 0.33 s median, 0.60 s p95 per
investigation; 0 calls outside the registry. The fake chooser's 90–91 % disconfirming rate reflects its injected
defects, not a model.

History (validation, control v1 top-1 / top-3): ADR-049 52 / 86 % · ADR-050 51 / 85 % · `investigation_v3` 48 / 70 %
(regressed: alphabetical tie-break with 5 hypotheses) · `investigation_v4` 55 / 88 %.

**Incident configuration.** The table above ran on the `IncidentConfig` defaults of the time (`G` = 1 h, `H` = 1 h, the
original chains; ADR-051 found the default differed from the ADR-042 choice). On `incidents_v4` (`G` = 6 h, `H` = 0,
`nesting = chains_plus`, ADR-052) there are ~10 % fewer incidents to investigate; validation (v1 / v2): control
55 / 55 % top-1, 88 / 85 % top-3; prior 57 / 58 %, 85 / 85 %; false-positive incidents → not a cause 83 / 73 %; narrowed
set hits the locus 42 / 40 % (Stage 4 top-5: 39 / 37 %); a decoy supports the leader 0 %; safety 100 %. Tuning
(v1 / v2): control 53 / 51 % top-1, 84 / 86 % top-3 (3 points lower top-1 than before; not analysed). Renewal
failures (validation v1): 11 / 50 % (n = 28). The incident sets differ, so before / after is approximate.

Control by scenario kind (validation, v1, top-1 / top-3): dunning failure, duplicate charge, refund spike 100 / 100 %;
checkout regression 84 / 95 %; correlated unrelated anomalies 81 / 100 %; country degradation 78 / 100 %; fraud-like
spike 71 / 79 %; recovery after degradation 67 / 100 %; pricing change 60 / 100 %; mix-shift masking 60 / 100 %;
gradual degradation 54 / 96 %; payment method 47 / 94 %; data pipeline 44 / 88 %; PSP degradation 40 / 100 %;
simultaneous incidents 36 / 97 %; **subscription renewal failure 10 / 45 %**; ambiguous signal 0 / 20 %.

## Known limitations

| Limitation | Evidence | Where it is addressed |
|---|---|---|
| Checks rarely change the leader: top-1 equals the prior's | control 55 % vs prior 56 % top-1; the gain is top-3 (+ 1–2 points) and false-positive handling (72–78 % vs 10–11 %) | owner decision (Gate 1): kept as a limitation; richer checks or Jev choices later |
| Renewal failures rarely lead | top-1 10–11 %, top-3 45–50 %; the renewal-metric candidate usually forms its own Stage 6 incident (different **metric group**, not a scope-dimension effect), so approval-only incidents give the renewal cause prior 1; when the channel split shows `unchanged`, `normal_variation` often stays ahead | grouping renewal with approval drops was tried and left off (wrong merges above the threshold, ADR-052); a cross-incident view for Stage 7 |
| Weak kinds | ambiguous signal 0 / 20 %, PSP degradation 40 % top-1, data pipeline 44 %, payment method 47 % | more discriminating checks |
| Side files add little to top-1 | control 55 % vs 54–55 % without them; honest signals cited in 16–17 % | richer side sources (real change logs) |
| Tuned on seen seeds | ADR-050 changes found with seeds 11–20 visible; the ADR-052 incident choice used them too | HELDOUT, Stage 9 |
| Jev not evaluated | OQ-1; the fake is a pipeline check | live run when access and a budget exist |
| No LLM explanation | template explainer only (ADR-049 D-7) | an LLM explainer behind the same port and validator |
