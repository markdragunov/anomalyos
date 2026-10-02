# Stage 4 — Cohort Intelligence and Evidence Narrowing

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
