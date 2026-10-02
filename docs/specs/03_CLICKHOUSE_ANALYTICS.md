# Stage 2 — ClickHouse Analytical Substrate

## Objective

Make ClickHouse the source of truth for analytical facts used by both Mode A and Mode B.

## Tables

At minimum:
- events
- customers
- payment_methods
- payment_intents
- charges
- refunds
- invoices
- subscriptions
- metric_snapshots
- anomalies
- decisions
- incidents
- evidence
- investigation_steps

Preserve object relationships; do not flatten everything into `payment_event`.

## Metrics

Payments:
- payment intent volume
- success rate
- failure rate
- charge success rate
- amount succeeded
- authorization/charge degradation

Billing:
- invoice payment success
- renewal success
- involuntary churn proxy
- amount due/paid
- refund rate
- duplicate-charge rate

Operational:
- latency
- retry rate
- 3DS/action rate
- PSP error rate

## Dimensions

Support:
- PSP
- country
- currency
- payment method
- card brand
- platform
- app version
- plan
- subscription status

## Query contracts

Create a fixed analytical query layer.

The AI layer receives results from named queries such as:

```text
get_metric_history()
get_metric_snapshot()
breakdown_by_dimension()
compare_cohorts()
get_related_metrics()
get_incident_window()
get_similar_incidents()
```

AI must not construct arbitrary SQL.

## Evidence IDs

Every analytical result must be serializable into an evidence object:

```text
evidence_id
type
query_name
time_window
dimensions
values
source_table
created_at
```

## Acceptance

- deterministic SQL fixtures
- correct metric definitions
- no accidental future-data leakage
- evidence references stable enough for audit
- Mode A and Mode B can consume compact query results
