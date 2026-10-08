# Task: ingestion members do not anchor other groups (follow-up of ADR-052)

Prepared: 2026-10-08  
Status: **Approved** by the owner (2026-10-08), brief and design together. Decisions start at ADR-053  
Branch: `ingestion-anchor` (from `main` after PR #15)

## Read first

`docs/INCIDENTS.md` (correlation, results, known limitations) → `docs/DECISIONS.md` (ADR-041 D-2, ADR-042, ADR-051,
ADR-052) → `docs/tasks/SCOPE-DIMENSIONS-DESIGN.md` (D-2, D-5) → `docs/INVARIANTS.md` (INV-006, INV-015).

Work in phases. Stop and report at every ⛔ Gate. Every report: what changed · decisions (ADR ids) · tests (passed /
failed / skipped, with reasons) · measurements · open questions · whether the next phase is authorized.

---

## Purpose

The `ingestion` metric group (`late_arrival_share`) is compatible with every group (ADR-041 D-2: late data moves every
first-look metric). Once an ingestion candidate is in an incident, it can anchor candidates of any group that nest with
its locus. On seed 16 (v1) under pair nesting, a `late_arrival_share` member on `{psp_beta}` anchored both a campaign
volume rise and an unrelated `{sepa_debit, psp_beta}` outage — the one campaign + outage merge that ruled pair nesting
out (ADR-052). Remove the hub and check whether pair nesting becomes safe.

## Facts to verify (from ADR-052, not re-measured for this rule)

- Current default `incidents_v4` (`nesting = chains_plus`): duplicates 0.75 / 0.75 (seeds 1–10), 0.67 / 0.68 (11–20);
  wrong merges 3.3 / 3.9 % and 3.0 / 3.7 %; no campaign + outage merge.
- `pairs` alone: 0.673 (seeds 1–10 pooled), 0.566 (11–20 pooled), wrong merges 3.9 %, one campaign + outage merge on
  seed 16 v1 through an ingestion member.
- How often an ingestion member is the anchor of a join, and of a wrong merge, has not been measured.

---

# Design (approved with the brief, 2026-10-08)

## D-1. The rule

A member whose metric groups are only `{"ingestion"}` anchors a candidate only if the candidate is in the `ingestion`
group too. An ingestion **candidate** still joins any incident through any member (late data explains every metric);
it just cannot pass that membership on. Implemented as an `IncidentConfig` switch `ingestion_anchors_others`
(`True` = today's behaviour) checked in `correlate.decide`; link evidence is unchanged otherwise.

**Known risk.** In a data-pipeline incident the late-arrival candidate is often the first member; a conversion or
approval drop of the same record then joins through it today (seed 16 trace). With the rule it needs another member to
nest with, so some correct joins may become duplicates. The evaluation measures this.

## D-2. Evaluation protocol — fixed before results

Script: extend `scripts/eval_duplicates_dev.py` with the switch (one pipeline run per world, refolds per variant;
refuses HELDOUT). Route-agnostic, `G` = 6 h, `H` = 0, A and C off.

1. Variants on tuning seeds 1–10 (v1 + v2 pooled): baseline = `chains_plus` (current default); `chains_plus` + rule;
   `pairs` + rule; `pairs` without the rule for reference.
2. Acceptance against the baseline: duplicates per covered record lower; wrong-merge rate at most + 1 point; campaign
   + outage merges 0; coverage at most − 1 point.
3. Among passing variants, fewer duplicates wins; within 0.02, the narrower (`chains_plus` before `pairs`).
4. The pick must also show **0 campaign + outage merges on seeds 11–20** (already seen for Stages 6–7, so a check, not a
   clean validation); otherwise keep the baseline and report.
5. If the default changes: Stage 7 control rerun (`scripts/eval_investigation_dev.py`, about 1.5 h), and the Gate 0
   duplicate classifier on seeds 11–20 for the new default; HELDOUT untouched.

Also reported: the number of joins and of wrong merges whose anchor is an ingestion member (baseline), and duplicates
by scenario kind for `data_pipeline_issue`.

---

# Phase 1 — Implementation and evaluation ⛔ Gate 1

Switch, unit tests (an ingestion member does not anchor an approval drop; an ingestion candidate still joins an
approval incident; ingestion anchors ingestion; the campaign + outage pair of seed 16 in miniature does not merge under
`pairs` with the rule), evaluation per D-2.

# Phase 2 — Documentation and PR

`docs/INCIDENTS.md` (rule, results, limitations), `docs/INVESTIGATION.md` if Stage 7 numbers change, ADR-053, PR
`ingestion-anchor` → `main`, not merged.

---

# Acceptance

- [ ] Brief and design approved; ADR-053 records the rule and the outcome.
- [ ] The switch, with tests; default changed only if D-2 says so.
- [ ] Duplicates, wrong merges, campaign + outage merges and coverage reported on both seed ranges; Stage 7 if the
      default changes; HELDOUT untouched.
- [ ] Harness and product tests green locally and in CI; PR open, not merged.
