"""Canonical metric definitions: one per (name, version), as data (docs/METRICS.md, ADR-026).

Why: every layer above (detection, cohorts, evaluation) must see the same numerator and
denominator for "authorization rate"; changing a definition bumps its version, never edits it.

A definition is a pair of SQL aggregate expressions over rows of ``events_norm`` (or over a
fixed source subquery), plus the dimensions the metric may be filtered or grouped by. The
fragments are static text owned by this module; all caller-supplied values are bound as query
parameters by ``compute`` (never interpolated). Fixed parameter names a fragment may use:
``{run_id:String}``, ``{start:UInt32}``, ``{end:UInt32}`` (epoch seconds), and the table name
placeholder ``{table}``.

Invariants: rates always carry numerator and denominator; a metric never reads ground truth
(INV-015) and never reads events at or after ``end`` (no future leakage).
"""

from __future__ import annotations

from dataclasses import dataclass

# Cohort dimensions (docs/DATA_MODEL.md) with their ClickHouse parameter types.
DIMENSION_TYPES: dict[str, str] = {
    "psp": "String", "payment_method_type": "String", "card_brand": "String",
    "customer_country": "String", "issuer_country": "String", "currency": "String",
    "platform": "String", "app_version": "String", "plan_id": "String", "channel": "String",
    "merchant_id": "String", "attempt_no": "UInt8",
}
ALLOWED_DIMENSIONS = tuple(DIMENSION_TYPES)

# Grain name -> (interval length, ClickHouse interval unit, seconds). Closed set: the unit is
# never taken from caller text.
GRAINS: dict[str, tuple[int, str, int]] = {
    "5m": (5, "MINUTE", 300), "15m": (15, "MINUTE", 900), "1h": (1, "HOUR", 3600), "1d": (1, "DAY", 86400),
}

_ATTEMPT_TYPES = "('payment.authorized', 'payment.declined', 'payment.failed')"
_ALL_DIMS = ALLOWED_DIMENSIONS
# Dimensions unknown on the event that anchors a metric (checkout.started has no card or issuer yet).
_CHECKOUT_DIMS = tuple(d for d in _ALL_DIMS if d not in ("payment_method_type", "card_brand", "issuer_country", "attempt_no"))
_BILLING_DIMS = ("psp", "customer_country", "currency", "plan_id", "channel", "merchant_id")


@dataclass(frozen=True)
class MetricDef:
    name: str
    version: int
    description: str
    numerator: str  # SQL aggregate expression
    denominator: str  # SQL aggregate expression
    allowed_dims: tuple[str, ...]
    where: str = "1"  # static predicate over source rows (event types, channel, ...)
    source: str = "{table}"  # table, or a fixed subquery text ending in an alias
    required_group_by: tuple[str, ...] = ()  # e.g. currency for money sums
    empty_denominator: int = 0  # denominator of a window with no source rows (0 for ratios, 1 for counts/sums)

    @property
    def key(self) -> tuple[str, int]:
        return (self.name, self.version)


_DUPLICATE_SOURCE = """(
    SELECT *, (idempotency_key_present = 0 AND prev_ts > 0 AND toUInt32(occurred_at) - prev_ts <= 60) AS is_duplicate
    FROM (
        SELECT *, toUInt32(lagInFrame(occurred_at, 1, toDateTime(0, 'UTC')) OVER (
            PARTITION BY customer_id, amount_minor, currency ORDER BY occurred_at, raw_seq
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)) AS prev_ts
        FROM {table}
        WHERE run_id = {run_id:String} AND event_type = 'payment.captured' AND channel = 'checkout'
          AND occurred_at >= toDateTime({start:UInt32} - 60, 'UTC') AND occurred_at < toDateTime({end:UInt32}, 'UTC')
    )
) AS dup"""

# Conversion as known when the checkout's window closed: the first authorization must have reached the store by then.
_CONVERSION_FIRST_LOOK_SOURCE = """(
    SELECT s.*, a.first_known AS first_auth_known
    FROM {table} AS s
    LEFT JOIN (
        SELECT payment_intent_id, min(ingested_at) AS first_known FROM {table}
        WHERE run_id = {run_id:String} AND event_type = 'payment.authorized' AND channel = 'checkout'
          AND occurred_at < toDateTime({end:UInt32}, 'UTC') AND ingested_at <= toDateTime({end:UInt32}, 'UTC')
        GROUP BY payment_intent_id
    ) AS a ON s.payment_intent_id = a.payment_intent_id
    WHERE s.run_id = {run_id:String} AND s.event_type = 'checkout.started'
) AS conv"""

# Rows windowed by *ingestion* time: `occurred_at` here is the delivery time, `event_time` the business time.
_LATE_ARRIVAL_SOURCE = """(
    SELECT t.run_id AS run_id, t.event_type AS event_type, t.ingested_at AS occurred_at, t.ingested_at AS ingested_at,
           t.occurred_at AS event_time, t.psp AS psp, t.customer_country AS customer_country, t.channel AS channel,
           t.merchant_id AS merchant_id
    FROM {table} AS t
    WHERE t.run_id = {run_id:String}
) AS ing"""

_REGISTRY: dict[tuple[str, int], MetricDef] = {}


def _register(*defs: MetricDef) -> None:
    for d in defs:
        if d.key in _REGISTRY:
            raise ValueError(f"duplicate metric definition {d.key}")
        bad = [x for x in (*d.allowed_dims, *d.required_group_by) if x not in DIMENSION_TYPES]
        if bad or any(x not in d.allowed_dims for x in d.required_group_by):
            raise ValueError(f"metric {d.key}: invalid dimensions {bad or d.required_group_by}")
        _REGISTRY[d.key] = d


_register(
    MetricDef(
        "authorization_rate", 1, "authorized / (authorized + declined + failed), per attempt",
        numerator="countIf(event_type = 'payment.authorized')", denominator="count()",
        where=f"event_type IN {_ATTEMPT_TYPES}", allowed_dims=_ALL_DIMS),
    MetricDef(
        "first_attempt_authorization_rate", 1, "authorization_rate restricted to attempt_no = 1",
        numerator="countIf(event_type = 'payment.authorized')", denominator="count()",
        where=f"event_type IN {_ATTEMPT_TYPES} AND attempt_no = 1", allowed_dims=_ALL_DIMS),
    MetricDef(
        "technical_failure_rate", 1, "payment.failed (technical) / all attempts",
        numerator="countIf(event_type = 'payment.failed')", denominator="count()",
        where=f"event_type IN {_ATTEMPT_TYPES}", allowed_dims=_ALL_DIMS),
    MetricDef(
        "checkout_conversion_rate", 1,
        "checkouts with at least one authorized payment before `end` / checkout.started, windowed by checkout start",
        numerator=("countIf(payment_intent_id IN (SELECT payment_intent_id FROM {table} WHERE run_id = {run_id:String} "
                   "AND event_type = 'payment.authorized' AND channel = 'checkout' "
                   "AND occurred_at >= toDateTime({start:UInt32}, 'UTC') AND occurred_at < toDateTime({end:UInt32}, 'UTC')))"),
        denominator="count()", where="event_type = 'checkout.started'", allowed_dims=_CHECKOUT_DIMS),
    MetricDef(
        "checkout_conversion_rate", 2,
        "checkout.started whose first authorization had reached the store by the end of the checkout's window / checkout.started "
        "(first look: no information from after the window closes)",
        numerator="countIf(first_auth_known > toDateTime(0, 'UTC') AND first_auth_known <= {window_end})",
        denominator="count()", source=_CONVERSION_FIRST_LOOK_SOURCE, allowed_dims=_CHECKOUT_DIMS),
    MetricDef(
        "subscription_cancellation_rate", 1,
        "subscription.canceled / subscription.renewal_attempted in the same window (cancellations at renewal and after failed dunning)",
        numerator="countIf(event_type = 'subscription.canceled')",
        denominator="countIf(event_type = 'subscription.renewal_attempted')",
        where="event_type IN ('subscription.canceled', 'subscription.renewal_attempted')", allowed_dims=_BILLING_DIMS),
    MetricDef(
        "subscription_cancellation_rate", 2,
        "voluntary cancellations at renewal / (renewal attempts + voluntary cancellations); involuntary cancellations "
        "after failed dunning are excluded (they trail the cause by a week)",
        numerator="countIf(event_type = 'subscription.canceled' AND status = 'canceled_voluntary')",
        denominator="countIf(event_type = 'subscription.renewal_attempted' OR (event_type = 'subscription.canceled' AND status = 'canceled_voluntary'))",
        where="event_type IN ('subscription.canceled', 'subscription.renewal_attempted')", allowed_dims=_BILLING_DIMS),
    MetricDef(
        "dunning_recovery_rate", 1,
        "renewal retries (+3 / +7 days) that authorize / renewal retries, counted at retry time",
        numerator="countIf(event_type = 'payment.authorized')", denominator="countIf(event_type = 'dunning.attempted')",
        where="channel = 'renewal' AND attempt_no > 1 AND event_type IN ('payment.authorized', 'dunning.attempted')",
        allowed_dims=_ALL_DIMS),
    MetricDef(
        "refund_count", 1,
        "number of refund.succeeded; denominator is the constant 1. Unlike refund_rate it does not move when this "
        "window's captures drop (refunds belong to older captures)",
        numerator="count()", denominator="1", where="event_type = 'refund.succeeded'",
        allowed_dims=("psp", "customer_country", "currency", "platform", "channel", "merchant_id"), empty_denominator=1),
    MetricDef(
        "late_arrival_share", 1,
        "events delivered more than 15 minutes after they occurred / events delivered, windowed by delivery time",
        numerator="countIf(ingested_at - event_time > 900)", denominator="count()", source=_LATE_ARRIVAL_SOURCE,
        allowed_dims=("psp", "customer_country", "channel", "merchant_id")),
    MetricDef(
        "renewal_success_rate", 1, "subscription.renewed / subscription.renewal_attempted (events counted in their own window)",
        numerator="countIf(event_type = 'subscription.renewed')",
        denominator="countIf(event_type = 'subscription.renewal_attempted')",
        where="event_type IN ('subscription.renewed', 'subscription.renewal_attempted')", allowed_dims=_BILLING_DIMS),
    MetricDef(
        "refund_rate", 1, "refund.succeeded / payment.captured in the same window (refunds lag captures)",
        numerator="countIf(event_type = 'refund.succeeded')", denominator="countIf(event_type = 'payment.captured')",
        where="event_type IN ('refund.succeeded', 'payment.captured')",
        allowed_dims=("psp", "customer_country", "currency", "platform", "channel", "merchant_id")),
    MetricDef(
        "duplicate_charge_rate", 1,
        "checkout captures without idempotency key that follow a same-customer, same-amount, same-currency capture within 60 s / checkout captures",
        numerator="countIf(is_duplicate)", denominator="count()", source=_DUPLICATE_SOURCE,
        allowed_dims=tuple(d for d in _ALL_DIMS if d != "channel")),
    MetricDef(
        "fraud_flag_rate", 1, "fraud.flagged / all attempts",
        numerator="countIf(event_type = 'fraud.flagged')", denominator="countIf(event_type IN " + _ATTEMPT_TYPES + ")",
        where=f"event_type IN ('fraud.flagged', 'payment.authorized', 'payment.declined', 'payment.failed')", allowed_dims=_ALL_DIMS),
    MetricDef(
        "attempt_volume", 1, "number of payment attempts; denominator is the constant 1",
        numerator="count()", denominator="1", where=f"event_type IN {_ATTEMPT_TYPES}", allowed_dims=_ALL_DIMS,
        empty_denominator=1),
    MetricDef(
        "revenue_collected_minor", 1, "sum of amount_minor of payment.captured, always per currency; denominator is the constant 1",
        numerator="sum(amount_minor)", denominator="1", where="event_type = 'payment.captured'", allowed_dims=_ALL_DIMS,
        required_group_by=("currency",), empty_denominator=1),
)


def get_metric(name: str, version: int) -> MetricDef:
    try:
        return _REGISTRY[(name, version)]
    except KeyError:
        known = sorted(f"{n} v{v}" for n, v in _REGISTRY)
        raise KeyError(f"unknown metric {name!r} v{version}; known: {known}") from None


def list_metrics() -> list[MetricDef]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
