# Stage 7 — design note (Gate 0)

Status: **accepted** (owner OK on 1–9, 2026-10-06; ADR-049). Jev is still blocked
(OQ-1): every Jev step runs on the fake client or is replaced by the deterministic control, and nothing here depends
on Jev quality.

## D-0. Scope of an investigation (brief 0.1)

- **Input:** one incident as known at `as_of` (snapshot, linked candidates with their latest Stage 4 analysis and
  bundle, Stage 5 decisions), the run, an `InvestigationConfig` (budgets, thresholds) and a choice source
  (`control` | `fake` | `replay` | `jev`).
- **Which incidents:** every incident, once, at its creation (`as_of = detected_at` of the incident). Digest items are
  not investigated (a digest drill-down needs a UI command, Stage 8). Evaluation also runs a **diagnostic** second
  pass at `+6 h`: Stage 4 labels are 63 % right at first look and 78–80 % at `+6 h` (`docs/INCIDENTS.md:103`), and the
  investigation's value may depend on it. Only the first pass is the product behaviour.
- **Output:** an `Investigation` — steps, evidence objects, hypotheses, stop reason, report — stored append-only (D-8).
- **Incident change:** a new engine event `investigation` (actor `system`). At start: `DETECTED → INVESTIGATING`
  (reason `investigation_started`, the transition ADR-028 reserved for Mode B); if the incident is already in another
  open state, no transition, only the field. At end: a row with `investigation_status` (`completed`, `handed_off`,
  `budget_exhausted`, `failed`) and the report id added to `evidence_ids`. Nothing else in the incident changes.
  This is the one Stage 6 code change (a new event kind in `incidents.engine`); correlation does not read it, so the
  second fold gives the same links.
- **Time:** tools read only data with `occurred_at < as_of` at first-look visibility (`window_close`), as Stage 4
  does. No clock reads in any decision; wall-clock is measured through an injected clock for the budget and the audit
  only (D-6).

## D-1. Stage A — evidence narrowing (brief 0.2)

The material Stage 4 does not show: the full pooled cohort tables of the incident's anchor candidate (the most
specific member, as in Stage 6 impact) over the approved combinations (`cohorts.config.COMBINATIONS`, ≤ 200 cohorts
each, through `cohorts.fetch.query_pooled` → `metrics.compute`).

- **Items:** one per tested cohort — dims, rate and composition effect, contribution, z, q-value, support; evidence
  id `evd_…` from `cohorts.analysis.evidence_id` (the same id Stage 4 gives the same cohort).
- **Chunks:** items of one combination, sorted by |contribution| desc then dims, cut into chunks of `chunk_size`
  (default 40). A selected chunk is split into `split` parts (default 4) and recursed into until a chunk has
  ≤ `leaf_size` items (default 10), to `max_depth` (default 3). At most `max_leaves` leaves (default 4) survive.
- **Importance:** control = chunk's summed |contribution| (top chunks win; ties by dims); Jev = a `noul` per chunk
  ("Does this group of cohorts hold most of the change?") over a typed chunk state (combination dims, share-of-change
  bucket, strongest-z bucket, support bucket, cohorts-moved bucket — no dimension values, no numbers).
- **Output:** the narrowed set = items of the surviving leaves (≤ 40) — the only cohort evidence Stage B starts from.

## D-2. Stage B — the loop (brief 0.3, ADR-019 option 3)

`OBSERVE → HYPOTHESES → SHORTLIST → CHOOSE → EXECUTE → EVALUATE → UPDATE → STOP / CONTINUE`.

- **State (typed, bounded, no free text):** incident features (metric families present, direction, locus dims,
  Stage 4 label, cohorts moved), per hypothesis (cause, status, supports / contradicts counts as buckets), remaining
  budget as buckets, the last step's outcome. Values from the closed enums of `jev.buckets` and D-4; serialized
  ≤ 2 KB; schema `investigation_state_v1`.
- **Check library:** each cause of vocabulary v1 has a fixed list of **checks** — a tool, closed-set arguments drawn
  from the incident's own evidence (its locus, its top cohorts, its controls, the metrics of the related table), and
  the outcome the cause **predicts** (`moved` / `unchanged` / `opposite`). Example, `psp_degradation`: approval of
  the locus PSP in the other countries `moved`; approval of the other PSPs in the locus country `unchanged`;
  `late_arrival_share` of the PSP `unchanged`. The full table is part of the ADR.
- **Shortlist:** ≤ `shortlist_size` (default 5) checks not yet run, ranked by how many of the top-3 hypotheses they
  separate (predictions that differ), then by the leading hypothesis' prior, then by a stable key. **Disconfirming
  rule:** the shortlist always contains at least one check whose predicted outcome under the **leading** hypothesis
  would contradict it if not observed; if none is left, the loop records it and stops with `no_disconfirming_check`.
- **Choice:** control = the top-ranked step. Jev = a `choice` over the shortlist step ids plus `stop` ("Which next
  check best separates the leading explanations?"), each option described in the state by typed fields (tool kind,
  hypothesis tested, predicted outcome); confidence below `choice_confidence_min` (default 0.5) → stop and hand off.
- **Repeats:** a step key = (tool, canonical arguments). Executed keys never enter the shortlist; a chosen key that was
  already run is rejected, counted, and stops the loop (it can only happen through a bug).

## D-3. Tools (brief 0.4)

Typed input (dataclass, closed-set arguments validated against the incident's evidence), typed bounded output, a stable
evidence id `evd_` + hash(tool, canonical args, run, `as_of`, metric version, config version), a timeout, one audit
row per call. Reads only through `metrics.compute`, `cohorts.fetch`, `incidents.impact` and the in-memory Stage 6
result (an `IncidentView` port; a typed ClickHouse reader of the Stage 6 tables is added to `incidents.storage` only
if a caller needs it).

| Tool | Arguments (closed sets) | Output |
|---|---|---|
| `get_incident` | — | the snapshot at `as_of` |
| `get_metric_history` | metric (incident's metrics + related), cohort (locus / scope / control), window (`episode`, `episode ± 24 h`, `7 d`) | per-window numerator, denominator, baseline band; summary bucket |
| `breakdown_by_dimension` | metric, dimension (an approved combination extending the locus) | top-k cohorts with rate / composition effect, z, q |
| `compare_cohorts` | metric, cohort A = locus, cohort B ∈ controls / siblings in the evidence | z of the difference, both rates, outcome `moved` / `unchanged` / `opposite` |
| `get_related_metrics` | cohort, metric ∈ the related table (`cohorts.config.RELATED_METRICS` + family table) | z per metric and outcome |
| `calculate_impact` | — | Stage 6 `impact.estimate` (lost revenue stays not provided, ADR-042) |
| `read_evidence` | evidence id ∈ the investigation's registry | the stored evidence object |
| `search_similar_incidents` | — | ≤ 5 earlier incidents of the run (created before `as_of`) sharing a metric group and a locus dimension: status, category, interval |
| `get_deployments` | service ∈ the services relevant to the incident's metric families (D-3a table), window ∈ {`onset ± 2 h`, `onset − 24 h … as_of`} | ≤ 10 deploys with `deployed_at ≤ as_of`: service, version, time relative to the incident start (bucket); outcome `present` / `absent` |
| `check_psp_status` | psp ∈ the PSPs in the incident's locus, top cohorts or controls | status entries with `posted_at ≤ as_of` overlapping `[start − 6 h, as_of]`: component, level, posted / resolved (only if `≤ as_of`); outcome `present` / `absent` |

Outcome thresholds, fixed now: `moved` if |z| ≥ 2 in the predicted direction, `opposite` if |z| ≥ 2 against it,
`unchanged` if |z| < 1, else `ambiguous` (counts neither way); fewer than 30 attempts in either period →
`insufficient`. **No mutation tool exists:** an architecture test fails if the registry holds a name from a mutation
list (`refund`, `retry`, `capture`, `cancel`, `disable`, `page`, `notify`, `write`, `update`, `delete`, `close`,
`resolve`) or a tool that takes a string outside its closed set. **Timeouts:** the investigation runner gets a
ClickHouse client created with `max_execution_time` (default 10 s); no change to `metrics.compute`.

## D-3a. Side files `deployments.json` and `psp_status.json` (owner decision 2026-10-06: built in Stage 7, ADR-033)

**Simulator (`simulation/side_files.py`, `sim-1.3.0`).** Generated after the event stream from the world, the release
train (`world.releases()`) and the scenario **effects** (mechanism × selector) — the same causal structure that
drives the events; the generator never reads `TruthSpec` / `GroundTruth`, causes or titles. Randomness only from
`derive_seed(seed, "side", …)`; no wall clock. Entries carry no scenario id, record key, cause label or truth text.

| Effect (mechanism × selector) | Honest signal | Probability | Timing |
|---|---|---|---|
| `ABANDON` × platform, app_version | the mobile release already in the train | 1.0 (it exists) | release time |
| `DUPLICATE` | deploy `api_gateway` | 0.8 | effect start − U(5 min, 2 h) |
| `APPROVAL` × channel renewal with `params.attempt_kind = "dunning"` | deploy `dunning_service` | 0.8 | same |
| `APPROVAL` × channel renewal (other) | deploy `renewal_job` | 0.8 | same |
| `REFUND` | deploy `refund_service` | 0.8 | same |
| `CHURN` | deploy `pricing_service` | 0.8 | same |
| `APPROVAL` × psp (any other dims except channel; e.g. PSP × country drifts) | status `authorization` degraded / partial outage on that PSP | 0.7 | posted onset + U(20, 90 min); resolved effect end + U(0, 60 min) |
| `APPROVAL` × a local payment method, no psp | status `local_methods` degraded on the method's PSP (`LOCAL_METHOD_PSP`) | 0.5 | same |
| `DELAY` × psp | status `webhooks` delayed on that PSP | 0.6 | same |
| `INJECT`, `VOLUME`, `APPROVAL` × country (with or without `payment_method_type = card`) | none | — | — |

Rules apply in table order, first match wins (renewal and dunning effects also carry a `psp` selector; both use the
selector `channel = renewal, psp, payment_method_type = card` and differ only by `params.attempt_kind`,
`simulation/scenarios.py:279` and `:436` — corrected at Gate 0 review).

**Open (Gate 0 review):** mechanism × selector alone also matches non-incident scenarios — `benign_shocks`
(`scenarios.py:623`, route `suppress`) and `ambiguous_signal` (`:527`, route `watch`, cause `unknown`) are `APPROVAL` ×
country × psp, so the table would give them an "honest" PSP status with probability 0.7, a side signal that ground truth
contradicts. Proposal: each `Effect` declares `params.side_signal` (`True` for operational faults, `False` for benign
shocks, the ambiguous dip, demand and promotion effects), set by the scenario author as part of the causal structure;
the generator emits an honest entry only when it is `True`, still without reading `TruthSpec` / `GroundTruth`.

Decoys (no effect on events): routine deploys of all services (`api_gateway`, `checkout_web`, `renewal_job`,
`dunning_service`, `refund_service`, `pricing_service`, `ledger`) at about 0.3 per service per day; one 2 h
maintenance window per PSP per week at night (`maintenance`); one minor `degraded performance` entry per PSP about
every 10 days, 30–120 min long. **Near-miss decoys:** for each effect without an honest signal of its kind, with
probability 0.3 an unrelated routine deploy within 2 h before its start, and with 0.2 a minor status entry on a random
PSP overlapping its onset — so "something happened near the onset" is not a clue on its own.

**Digests and versions.** `GENERATOR_VERSION = sim-1.3.0`; `events_sha256` stays equal to sim-1.2.2 (the run id is not
in event lines); the run id changes (it hashes the generator version), so DEV worlds are regenerated. Ground truth
gains `side_signals` per record (ids of the honest entries, evaluation only), so `truth_digest` changes; the golden
test pins a third digest over both side files. Validator: every honest id exists; no side entry text names a cause.

**Storage and readers.** The loader writes `<db>.deployments` and `<db>.psp_status` (the events database, not
`<db>_truth`; partitioned by `run_id`). A new package `src/pulseos/context/` holds two typed readers with static SQL and
bound parameters (`deployments(runner, db, run_id, services, start, end, as_of)`, `psp_status(runner, db, run_id, psps,
start, end, as_of)`), applying the as-of rules above — a top-level package, so it goes into the ADR.

**Checks.** A relevant deploy or status entry `present` supports the matching cause; `absent` is `ambiguous` (signals
are missing on purpose, and status pages post late), never a contradiction. A present entry in a service or PSP that
does not match a hypothesis neither supports nor contradicts it.

**Guard against measuring the simulator.** Evaluation runs the control twice — with and without the two side-file
tools — and reports the difference per cause, so the share of conclusions that rest on side files is visible.

## D-4. Hypotheses (brief 0.5)

- **Set:** the causes of vocabulary v1 with a non-zero prior, at most 5, plus `normal_variation` and `unknown`
  always present.
- **Record:** `hypothesis_id` (hash of investigation id + cause), cause, statement template id, supporting and
  contradicting evidence ids (contradicting ones are never dropped), `support_probability`, `confidence`, status
  (`open` → `supported` | `contradicted` | `insufficient_evidence`), next question (the next check's id).
- **Control prior (deterministic):** a table metric family × direction × locus dimensions → cause, e.g. approval down
  in `psp` → `psp_degradation`; in `payment_method_type` → `payment_method_degradation`; in `customer_country` →
  `issuer_or_country_degradation`; conversion down in `platform` / `app_version` → `checkout_regression`; renewal down
  → `renewal_job_failure`; dunning down → `dunning_failure`; refunds up → `refund_process_change`; duplicates up →
  `duplicate_charging`; fraud up or volume up with approval down → `fraud_attack`; cancellations up →
  `pricing_or_plan_change`; late arrivals up → `data_pipeline_issue`; Stage 4 label `mix_shift` → `normal_variation`.
  Prior weights 3 / 1 / 0 (match / family only / none).
- **Control update:** support score = prior + supports − 2 × contradicts. Status `supported` at ≥ 2 supports and 0
  contradicts; `contradicted` at ≥ 1 contradict and more contradicts than supports.
- **Jev:** a `noul` per hypothesis ("Is the evidence consistent with this cause?") over the state; fake now.
- **Language:** templates say "consistent with", "evidence against", "insufficient evidence"; never "caused by".

## D-5. Budgets and stop conditions (brief 0.6, INV-011)

| Budget | Default (proposal; owner sets values after the first measurements, spec 08) |
|---|---|
| Tool calls | 12 |
| Jev calls | 40 (narrowing ≤ 16, steps ≤ 12, hypotheses ≤ 12) |
| Evidence items | 60 |
| Repeated queries | 0 executed (an attempt stops the loop) |
| Wall-clock | 120 s per investigation; 10 s per query |

Stops: `sufficient_evidence` (leader `supported` and the runner-up contradicted or ≥ 2 supports behind),
`strong_contradiction` (every hypothesis with a prior contradicted → conclusion `unknown`), `false_positive`
(`normal_variation` supported), `budget_exhausted`, `tool_failure` (a failed check is excluded; the second failure
stops), `model_failure` (an unverified Jev answer stops; no fallback to the control within a run, so the two
systems stay separable), `low_confidence` (hand-off), `no_checks_left`, `no_disconfirming_check`. Every stop returns
the partial structured state; nothing raises out of the loop.

## D-6. Determinism and time

Same incident, evidence, config and choice source ⇒ same steps, evidence ids and report, except when the wall-clock
cap fires (recorded as `budget_exhausted: wall_clock`). Tests and evaluation use a step clock (each query costs a
fixed amount) so they are reproducible; real runs record measured durations for cost reporting.

## D-7. Explanation (brief 0.7, ADR-027 option 3, INV-008, INV-013)

- `explanation` package: an `Explainer` port; `TemplateExplainer` renders structured **claims** from the typed state:
  incident summary, narrowed evidence, hypotheses with supporting / contradicting ids, timeline, tool trace.
- A claim = template id + **slots**, each slot = (evidence id, field path) + epistemic label (`observed` /
  `inferred` / `estimated` / `recommended`). Numbers appear only through slots; there is no literal number in a
  template.
- **Citation validator** (runs before a report is returned): every claim has ≥ 1 evidence id; every id is in the
  investigation's registry; every slot's field exists in that evidence; the label is from the enum and an `estimated`
  value comes from an `estimated` field. A failed report is stored with its errors and the incident gets
  `investigation_status = failed`; no report is shown.

## D-8. Storage and packages (brief 0.8)

Packages `src/pulseos/investigation/` (narrowing, state, checks, shortlist, loop, tools, hypotheses, budgets,
storage), `src/pulseos/explanation/` (port, templates, validator) and `src/pulseos/context/` (the two side-file
readers, D-3a); `investigation` and `explanation` leave the stage guard, `context` is a new package in the ADR. `agent` and
`investigation_agent.py` stay forbidden. The `jev` package gains a second question set
(`investigation_question_set_v1`) and a generic request builder; question set v1 and `jev_state_v2` are unchanged.
Append-only ClickHouse tables, DDL in the ADR:

| Table | Row |
|---|---|
| `investigation_steps` | one per step (narrowing and loop): ids, seq, phase, state hash, shortlist, choice and its source, Jev request hash / verified / reasons / confidence, tool, args, evidence id, outcome, budget counters, measured times |
| `investigation_evidence` | one per evidence object: id, tool, args, payload, epistemic label, `as_of` |
| `investigation_reports` | one per investigation: hypotheses, stop reason, claims, validation result, budgets used |

## D-9. Evaluation protocol (brief 0.9, fixed before results)

DEV seeds only, HELDOUT untouched; 20 seeds × realism v1 / v2; Stage 6 route-agnostic incidents (the most to
investigate), `baseline_v2` incidents reported separately. Each engine incident's main record = the Stage 6 rule
(`evaluation.incidents._main`).

- **Systems:** (0) **prior only** — the control prior with no tools (what Stages 4–6 already know);
  (1) **deterministic control** — the reference; (1b) **control without side-file tools** — shows how much rests on
  `deployments` / `psp_status` (D-3a); (2) **fake pipeline** — a pipeline check only. Jev: "blocked".
- **Hypotheses:** true cause (`record.true_cause`) at top-1 / top-3 of the final ranking; share of investigations
  where the truth is not the leader but has its contradicting evidence recorded.
- **Side files:** share of investigations whose record has `side_signals` that cite one of them; share that cite a
  decoy as support for the leading hypothesis.
- **Narrowing:** root-cause locus or an affected cohort (ADR-038 equivalence, `evaluation.cohorts.equivalent_exact`)
  present in the narrowed set; compared with Stage 4 `top_cohorts` (k = 5).
- **False-positive incidents** (main record none or `normal_variation`): share concluded `normal_variation` or
  `unknown` / `insufficient_evidence`.
- **Safety:** citation validity 100 %, mutation calls 0, executed repeats 0, budgets never exceeded, a disconfirming
  check in every investigation that ran ≥ 1 step.
- **Cost:** steps, tool calls, Jev calls, evidence items, measured wall-clock per investigation (median, p95).
- **Tuning:** thresholds above fixed now; only budget defaults are chosen on seeds 1–10 (the smallest budget whose
  top-3 is within 1 point of the uncapped run), reported on 11–20. Per scenario kind throughout.

## D-10. Scope

As the brief: no generative explanation, UI, API, notifications, writes to billing state, incident closure, Jev live
calls, HELDOUT, new dependencies, free text from data in any state. In scope since the owner's decision on D-3a: the
simulator change for the side files (`sim-1.3.0`, event digests unchanged) and their loader tables and readers.

## Open questions for the owner

1. **D-0:** investigate every incident once at creation (product), plus a diagnostic pass at `+6 h` in evaluation?
2. **D-0:** the one Stage 6 change — a new engine event `investigation` with `DETECTED → INVESTIGATING`?
3. **D-1:** narrowing over the anchor's pooled cohort tables, chunk 40 / split 4 / leaf 10 / depth 3 / 4 leaves?
4. **D-2 / D-4:** check library with predicted outcomes per cause, control prior table and update rule as proposed?
5. **D-3a:** side files as specified — honest-signal table and probabilities, decoys and near-miss decoys, `sim-1.3.0`
   with event digests unchanged, `side_signals` in ground truth, package `context`, and the with / without ablation?
   Plus the Gate 0 review item: `params.side_signal` per effect so benign and ambiguous scenarios get no honest signal?
6. **D-5:** budget defaults as starting values; model failure stops (no fallback to the control within a run)?
7. **D-7:** slot-based templates with the citation validator as the only explainer?
8. **D-8:** packages `investigation` and `explanation`, three append-only tables, question set
   `investigation_question_set_v1`?
9. **D-9:** systems (prior only / control / fake), metrics and the budget tuning rule as proposed?
