# Stage 4 — Cohort Intelligence and Evidence Narrowing

> **Status:** pending Stage 3 (`STATUS.md`). Amended per the specs review and ADR-026.

## Objective

Turn aggregate anomalies into compact, high-value evidence.

Question:
> Where is the anomaly actually happening?

## Dimensions

- PSP
- country
- payment method
- card brand
- platform
- app version
- plan
- currency

## Cohort analysis

For each allowed dimension:
1. current metric
2. baseline
3. delta
4. sample size
5. contribution to aggregate change
6. reliability
7. control comparison

Do not rank by percentage decline alone.

## Controlled multi-dimensional drilldown

Support only approved combinations:

```text
PSP × Country
PSP × Card Brand
Country × Payment Method
PSP × Country × Platform
```

Use minimum sample sizes and bounded cardinality.

## Evidence narrowing

Create an `EvidenceBundle` that contains only the strongest evidence required by the decision layer.

Example:

```json
{
  "bundle_id": "evb_123",
  "anomaly": {...},
  "top_cohorts": [...],
  "controls": [...],
  "related_metrics": [...],
  "impact": {...}
}
```

The goal is not to maximize context.

The goal is:
> maximize decision-relevant signal while keeping state tight.

This follows the JevOps lesson that oversized state can degrade decision quality.

## Output

Return:
- strongest affected cohorts
- strongest controls
- competing explanations
- evidence IDs
- contribution estimates
- uncertainty flags

## Acceptance

- large cohorts are not dominated by tiny percentage swings
- control cohorts are explicit
- evidence bundle has bounded size
- all claims trace to analytical evidence

## Amendments (from review and ADR-026)

- **Rate vs mix.** "Contribution to aggregate change" is decomposed into a *rate effect* (the cohort's own metric moved) and a *composition effect* (the cohort's share of volume moved). A change explained only by composition is reported as such (mix shift / Simpson's paradox). The DE-campaign + iDEAL pair is the required test.
- **Multiple testing.** Eight dimensions and their approved combinations produce many cohorts; some will look anomalous by chance. Apply a correction or a minimum-support rule, stated in configuration, and report the number of cohorts examined.
- **Pooled windows, support gate.** Cohorts are evaluated pooled over the candidate window; fewer than 30 attempts → `insufficient_data`, never ranked (ADR-026).
- **Approved combinations are configuration**, versioned and reviewed, not code constants. Each addition is a typed-interface change (`INV-003`).
- **Isolation.** No ground truth in cohort code (see spec 03).
