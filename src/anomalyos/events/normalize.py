"""Raw Stripe-shaped events -> normalized DATA_MODEL envelope (ADR-022 option A, ADR-025).

Why: metrics, detection, cohorts and evaluation must depend on one logical contract, not on a
vendor-shaped source. The raw layer stays exactly as generated; this layer is a pure, versioned
mapping on top of it.

Input: raw event envelopes in stream order (``events.jsonl.gz`` order). Output: zero, one or
two normalized rows per raw event. Pure: no I/O, no clock, no randomness; the only memory is
per-object state (attempt numbering per PaymentIntent, idempotency signal, plan lookup).

Invariants:
* the same stream always yields identical rows (``NORMALIZATION_VERSION`` bumps on any change);
* every row carries ``raw_event_id`` and ``raw_seq`` so it can be traced to the raw layer;
* a dimension that does not apply is the explicit value ``unknown`` (never ``''``);
* an unknown raw type, an unknown failure code or an unexpected event shape raises
  ``NormalizationError`` (no silent drops);
* no run, scenario or ground-truth identifier is derived from event content; ``run_id`` is
  passed in and only partitions the table (INV-015).

Failure modes: out-of-order input (a charge before its PaymentIntent) raises; raw types that
are intentionally unmapped are listed in ``INTENTIONALLY_UNMAPPED`` so new types fail loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping

NORMALIZATION_VERSION = "1.0.0"
SCHEMA_VERSION = "norm-1"
MERCHANT_ID = "mer_sim_001"  # single synthetic merchant until multi-merchant (ADR-025)
UNKNOWN = "unknown"

# Closed vocabulary (docs/DATA_MODEL.md). `payment.attempted` and `chargeback.opened` are in the
# vocabulary but are not produced: attempts are counted from authorized/declined/failed, and the
# simulator generates no chargebacks yet.
EVENT_TYPES = frozenset({
    "checkout.started", "payment.authorized", "payment.declined", "payment.failed", "payment.captured",
    "invoice.created", "invoice.paid", "invoice.payment_failed", "subscription.renewal_attempted",
    "subscription.renewed", "subscription.past_due", "dunning.attempted", "subscription.canceled",
    "refund.requested", "refund.succeeded", "fraud.flagged",
})

# Raw types that produce no normalized row (entity or state change only). Listed explicitly so an
# unknown raw type is an error, not a silent drop.
INTENTIONALLY_UNMAPPED = frozenset({
    "customer.created", "payment_method.attached", "customer.subscription.created",
    "invoice.finalized", "invoice.marked_uncollectible",
    "payment_intent.requires_action", "payment_intent.succeeded", "payment_intent.payment_failed",
    "payment_intent.canceled",
    "charge.refunded",  # state change that duplicates refund.created
})

MAPPED_RAW_TYPES = frozenset({
    "payment_intent.created", "charge.succeeded", "charge.failed", "invoice.created", "invoice.paid",
    "invoice.payment_failed", "customer.subscription.updated", "customer.subscription.deleted",
    "refund.created",
})

ISSUER_FAILURE_CODES = frozenset({"card_declined", "expired_card", "incorrect_cvc", "payment_method_provider_decline"})
TECHNICAL_FAILURE_CODES = frozenset({"processing_error"})
FRAUD_DECLINE_REASON = "highest_risk_level"

NORM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "LowCardinality(String)"),
    ("event_id", "String"),
    ("event_type", "LowCardinality(String)"),
    ("schema_version", "LowCardinality(String)"),
    ("occurred_at", "DateTime('UTC')"),
    ("ingested_at", "DateTime('UTC')"),
    ("merchant_id", "LowCardinality(String)"),
    ("entity_id", "String"),
    ("customer_id", "String"),
    ("payment_intent_id", "String"),
    ("customer_country", "LowCardinality(String)"),
    ("issuer_country", "LowCardinality(String)"),
    ("currency", "LowCardinality(String)"),
    ("psp", "LowCardinality(String)"),
    ("payment_method_type", "LowCardinality(String)"),
    ("card_brand", "LowCardinality(String)"),
    ("platform", "LowCardinality(String)"),
    ("app_version", "LowCardinality(String)"),
    ("plan_id", "LowCardinality(String)"),
    ("channel", "LowCardinality(String)"),
    ("attempt_no", "UInt8"),
    ("amount_minor", "Int64"),
    ("status", "LowCardinality(String)"),
    ("decline_code", "LowCardinality(Nullable(String))"),
    ("error_code", "LowCardinality(Nullable(String))"),
    ("idempotency_key_present", "UInt8"),
    ("raw_event_id", "String"),
    ("raw_seq", "UInt64"),
)
NORM_COLUMN_NAMES = tuple(c for c, _ in NORM_COLUMNS)
DIMENSION_COLUMNS = ("customer_country", "issuer_country", "currency", "psp", "payment_method_type", "card_brand",
                     "platform", "app_version", "plan_id", "channel")


class NormalizationError(ValueError):
    """Raised for anything the mapping does not explicitly cover."""


@dataclass
class _PaymentIntentState:
    channel: str
    idempotency_key_present: bool
    plan_id: str
    attempts: int = 0


def _s(value: Any) -> str:
    return UNKNOWN if value in (None, "") else str(value)


class Normalizer:
    """Stateful mapper: feed raw events in stream order, get normalized rows back."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._pi: dict[str, _PaymentIntentState] = {}
        self._sub_plan: dict[str, str] = {}
        self._invoice_plan: dict[str, str] = {}

    # ------------------------------------------------------------------ helpers
    def _row(self, env: Mapping[str, Any], seq: int, event_type: str, entity_id: str, **fields: Any) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:  # guards the code against vocabulary drift
            raise NormalizationError(f"{event_type!r} is not in the event vocabulary")
        occurred = env["created"]
        row: dict[str, Any] = {
            "run_id": self.run_id,
            "event_id": f"{env['id']}/{event_type}",
            "event_type": event_type,
            "schema_version": SCHEMA_VERSION,
            "occurred_at": occurred,
            "ingested_at": occurred,  # raw stream has no ingestion delay (ADR-025)
            "merchant_id": MERCHANT_ID,
            "entity_id": entity_id,
            "customer_id": UNKNOWN, "payment_intent_id": UNKNOWN,
            "customer_country": UNKNOWN, "issuer_country": UNKNOWN, "currency": UNKNOWN, "psp": UNKNOWN,
            "payment_method_type": UNKNOWN, "card_brand": UNKNOWN, "platform": UNKNOWN, "app_version": UNKNOWN,
            "plan_id": UNKNOWN, "channel": UNKNOWN,
            "attempt_no": 0,  # 0 = not an attempt event
            "amount_minor": 0, "status": UNKNOWN, "decline_code": None, "error_code": None,
            "idempotency_key_present": 0,
            "raw_event_id": env["id"], "raw_seq": seq,
        }
        row.update(fields)
        for col in DIMENSION_COLUMNS:
            row[col] = _s(row[col])
        return row

    def _pi_state(self, pi_id: str) -> _PaymentIntentState:
        try:
            return self._pi[pi_id]
        except KeyError:
            raise NormalizationError(f"event references {pi_id} before its payment_intent.created") from None

    # ------------------------------------------------------------------ public
    def feed(self, seq: int, env: Mapping[str, Any]) -> list[dict[str, Any]]:
        etype = env["type"]
        obj = env["data"]["object"]
        if etype in INTENTIONALLY_UNMAPPED:
            self._observe_unmapped(etype, obj)
            return []
        if etype == "payment_intent.created":
            return self._payment_intent_created(seq, env, obj)
        if etype in ("charge.succeeded", "charge.failed"):
            return self._charge(seq, env, obj)
        if etype in ("invoice.created", "invoice.paid", "invoice.payment_failed"):
            return self._invoice(seq, env, obj)
        if etype == "customer.subscription.updated":
            return self._subscription_updated(seq, env, obj)
        if etype == "customer.subscription.deleted":
            return [self._row(env, seq, "subscription.canceled", obj["id"], **self._subscription_fields(obj), status="canceled")]
        if etype == "refund.created":
            return self._refund(seq, env, obj)
        raise NormalizationError(f"unknown raw event type {etype!r}")

    # ------------------------------------------------------------------ state-only observations
    def _observe_unmapped(self, etype: str, obj: Mapping[str, Any]) -> None:
        # subscription.created carries the plan used by later invoices/payment intents.
        if etype == "customer.subscription.created":
            self._sub_plan[obj["id"]] = _s((obj.get("metadata") or {}).get("plan"))

    # ------------------------------------------------------------------ mappings
    def _payment_intent_created(self, seq: int, env: Mapping[str, Any], obj: Mapping[str, Any]) -> list[dict[str, Any]]:
        meta = obj.get("metadata") or {}
        channel = meta.get("channel")
        if channel not in ("checkout", "renewal"):
            raise NormalizationError(f"payment_intent {obj['id']} has unexpected channel {channel!r}")
        plan = self._invoice_plan.get(obj.get("invoice") or "", UNKNOWN)
        idem = env["request"]["idempotency_key"] is not None
        self._pi[obj["id"]] = _PaymentIntentState(channel=channel, idempotency_key_present=idem, plan_id=plan)
        etype = "checkout.started" if channel == "checkout" else "subscription.renewal_attempted"
        return [self._row(
            env, seq, etype, obj["id"], customer_id=obj["customer"], payment_intent_id=obj["id"],
            customer_country=meta.get("customer_country"), currency=obj["currency"], psp=meta.get("psp"),
            platform=meta.get("platform"), app_version=meta.get("app_version"), plan_id=plan, channel=channel,
            amount_minor=obj["amount"], status="started", idempotency_key_present=int(idem))]

    def _charge(self, seq: int, env: Mapping[str, Any], obj: Mapping[str, Any]) -> list[dict[str, Any]]:
        state = self._pi_state(obj["payment_intent"])
        state.attempts += 1
        if state.attempts > 255:
            raise NormalizationError(f"{obj['payment_intent']} has more than 255 attempts")
        meta = obj.get("metadata") or {}
        pmd = obj.get("payment_method_details") or {}
        pm_type = pmd.get("type")
        common: dict[str, Any] = dict(
            customer_id=obj["customer"], payment_intent_id=obj["payment_intent"],
            customer_country=meta.get("customer_country"), issuer_country=(pmd.get(pm_type) or {}).get("country"),
            currency=obj["currency"], psp=meta.get("psp"), payment_method_type=pm_type,
            card_brand=(pmd.get("card") or {}).get("brand"), platform=meta.get("platform"),
            app_version=meta.get("app_version"), plan_id=state.plan_id, channel=state.channel,
            attempt_no=state.attempts, amount_minor=obj["amount"],
            idempotency_key_present=int(state.idempotency_key_present))
        rows: list[dict[str, Any]] = []
        if env["type"] == "charge.succeeded":
            rows.append(self._row(env, seq, "payment.authorized", obj["id"], status="authorized", **common))
            rows.append(self._row(env, seq, "payment.captured", obj["id"], status="captured", **common))
        else:
            code = obj.get("failure_code")
            reason = (obj.get("outcome") or {}).get("reason")
            outcome = dict(decline_code=reason, error_code=code)
            if reason == FRAUD_DECLINE_REASON:
                rows.append(self._row(env, seq, "fraud.flagged", obj["id"], status="flagged", **outcome, **common))
                rows.append(self._row(env, seq, "payment.declined", obj["id"], status="declined", **outcome, **common))
            elif code in ISSUER_FAILURE_CODES:
                rows.append(self._row(env, seq, "payment.declined", obj["id"], status="declined", **outcome, **common))
            elif code in TECHNICAL_FAILURE_CODES:
                rows.append(self._row(env, seq, "payment.failed", obj["id"], status="failed", **outcome, **common))
            else:
                raise NormalizationError(f"charge {obj['id']} has unmapped failure_code {code!r}")
        if state.channel == "renewal" and state.attempts > 1:
            rows.append(self._row(env, seq, "dunning.attempted", obj["id"], status="attempted", **common))
        return rows

    def _invoice(self, seq: int, env: Mapping[str, Any], obj: Mapping[str, Any]) -> list[dict[str, Any]]:
        plan = self._sub_plan.get(obj["subscription"], UNKNOWN)
        self._invoice_plan[obj["id"]] = plan
        status = {"invoice.created": "created", "invoice.paid": "paid", "invoice.payment_failed": "payment_failed"}[env["type"]]
        return [self._row(
            env, seq, env["type"], obj["id"], customer_id=obj["customer"],
            customer_country=(obj.get("customer_address") or {}).get("country"), currency=obj["currency"],
            plan_id=plan, channel="renewal", amount_minor=obj["amount_due"], status=status)]

    def _subscription_fields(self, obj: Mapping[str, Any]) -> dict[str, Any]:
        price = obj["items"]["data"][0]["price"]
        meta = obj.get("metadata") or {}
        return dict(customer_id=obj["customer"], customer_country=meta.get("customer_country"), currency=price["currency"],
                    plan_id=meta.get("plan"), channel="renewal", amount_minor=price["unit_amount"])

    def _subscription_updated(self, seq: int, env: Mapping[str, Any], obj: Mapping[str, Any]) -> list[dict[str, Any]]:
        prev = env["data"].get("previous_attributes") or {}
        fields = self._subscription_fields(obj)
        if "current_period_start" in prev and obj["status"] == "active":
            return [self._row(env, seq, "subscription.renewed", obj["id"], status="renewed", **fields)]
        if obj["status"] == "past_due":
            return [self._row(env, seq, "subscription.past_due", obj["id"], status="past_due", **fields)]
        raise NormalizationError(f"subscription.updated {obj['id']} has an unmapped shape (status={obj['status']!r}, previous={sorted(prev)})")

    def _refund(self, seq: int, env: Mapping[str, Any], obj: Mapping[str, Any]) -> list[dict[str, Any]]:
        meta = obj.get("metadata") or {}
        common: dict[str, Any] = dict(
            payment_intent_id=obj["payment_intent"], customer_country=meta.get("customer_country"), currency=obj["currency"],
            psp=meta.get("psp"), platform=meta.get("platform"), channel=meta.get("channel"), amount_minor=obj["amount"],
            decline_code=None, error_code=None)
        rows = [self._row(env, seq, "refund.requested", obj["id"], status="requested", **common)]
        if obj["status"] == "succeeded":
            rows.append(self._row(env, seq, "refund.succeeded", obj["id"], status="succeeded", **common))
        elif obj["status"] != "pending":
            raise NormalizationError(f"refund {obj['id']} has unmapped status {obj['status']!r}")
        return rows


def normalize(events: Iterable[tuple[int, Mapping[str, Any]]], run_id: str) -> Iterator[dict[str, Any]]:
    """Normalize ``(seq, envelope)`` pairs in stream order. Pure and deterministic."""
    n = Normalizer(run_id)
    for seq, env in events:
        yield from n.feed(seq, env)
