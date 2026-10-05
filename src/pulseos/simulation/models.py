"""Typed, immutable domain objects of the synthetic billing world (Stripe-shaped).

Why: later stages (detection, cohorts, investigation) must reason over data that looks like a
real PSP/billing event stream. The shapes follow Stripe's public object model (prefixes,
lifecycle statuses, ``event`` envelope) so the data feels familiar; we do **not** claim
compatibility with Stripe's API or any version of it.

Input:  field values produced by :mod:`pulseos.simulation.engine`.
Output: frozen dataclasses; ``to_dict()`` yields the JSON snapshot embedded in events.

Invariants
* Objects are frozen. A lifecycle transition is a *new* instance (``dataclasses.replace``); the
  event that announces it embeds a serialized snapshot, so an emitted event can never change.
* Money is integer minor units + lowercase ISO 4217 code (Stripe convention). Zero-decimal
  currencies (``jpy``) are stored as whole units, as Stripe does.
* Timestamps are integer Unix seconds, UTC.
* No payment credentials: no PAN, CVC, expiry, IBAN, account/routing numbers, not even last4.
  Card details carry brand / issuer country / funding only. ``validate.scan_sensitive`` enforces.
* Simulator-only attributes (PSP route, client platform/app version, channel) live in
  ``metadata``, the place a merchant would put them. Ground truth never appears in objects.

Failure modes: none at runtime; construction is plain data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

API_VERSION = "2026-09-01.anomalyos-sim"

ZERO_DECIMAL_CURRENCIES = frozenset({"jpy"})


def canonical_json(obj: Any) -> str:
    """Stable, compact JSON. Dict order is construction order, which is deterministic."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


Meta = Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Customer:
    id: str
    created: int
    country: str
    segment: str  # "consumer" | "business"

    object_type = "customer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "customer",
            "created": self.created,
            "livemode": False,
            "address": {"country": self.country},
            "metadata": {"segment": self.segment},
        }


@dataclass(frozen=True, slots=True)
class PaymentMethod:
    id: str
    created: int
    customer: str
    type: str  # card | sepa_debit | ideal | pix
    country: str  # issuer / bank country
    card_brand: str | None = None
    card_funding: str | None = None
    fingerprint: str = ""

    object_type = "payment_method"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "object": "payment_method",
            "created": self.created,
            "livemode": False,
            "customer": self.customer,
            "type": self.type,
        }
        if self.type == "card":
            d["card"] = {
                "brand": self.card_brand,
                "country": self.country,
                "funding": self.card_funding,
                "fingerprint": self.fingerprint,
            }
        else:
            d[self.type] = {"country": self.country, "fingerprint": self.fingerprint}
        return d


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    id: str
    created: int
    amount: int
    currency: str
    customer: str
    payment_method: str
    status: str  # requires_payment_method|requires_confirmation|requires_action|processing|succeeded|canceled
    metadata: Meta
    invoice: str | None = None
    latest_charge: str | None = None
    amount_received: int = 0
    last_payment_error: Mapping[str, Any] | None = None
    cancellation_reason: str | None = None
    canceled_at: int | None = None

    object_type = "payment_intent"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "payment_intent",
            "created": self.created,
            "livemode": False,
            "amount": self.amount,
            "amount_received": self.amount_received,
            "currency": self.currency,
            "customer": self.customer,
            "payment_method": self.payment_method,
            "capture_method": "automatic",
            "status": self.status,
            "invoice": self.invoice,
            "latest_charge": self.latest_charge,
            "last_payment_error": dict(self.last_payment_error) if self.last_payment_error else None,
            "cancellation_reason": self.cancellation_reason,
            "canceled_at": self.canceled_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Charge:
    id: str
    created: int
    amount: int
    currency: str
    customer: str
    payment_intent: str
    payment_method: str
    payment_method_type: str
    status: str  # succeeded | failed
    outcome: Mapping[str, Any]
    metadata: Meta
    card_brand: str | None = None
    card_country: str | None = None
    card_funding: str | None = None
    failure_code: str | None = None
    invoice: str | None = None
    amount_refunded: int = 0

    object_type = "charge"

    @property
    def refunded(self) -> bool:
        return self.amount_refunded >= self.amount and self.amount > 0

    def to_dict(self) -> dict[str, Any]:
        pmd: dict[str, Any] = {"type": self.payment_method_type}
        if self.payment_method_type == "card":
            pmd["card"] = {"brand": self.card_brand, "country": self.card_country, "funding": self.card_funding}
        else:
            pmd[self.payment_method_type] = {"country": self.card_country}
        ok = self.status == "succeeded"
        return {
            "id": self.id,
            "object": "charge",
            "created": self.created,
            "livemode": False,
            "amount": self.amount,
            "amount_captured": self.amount if ok else 0,
            "amount_refunded": self.amount_refunded,
            "currency": self.currency,
            "customer": self.customer,
            "payment_intent": self.payment_intent,
            "payment_method": self.payment_method,
            "payment_method_details": pmd,
            "invoice": self.invoice,
            "status": self.status,
            "paid": ok,
            "captured": ok,
            "refunded": self.refunded,
            "failure_code": self.failure_code,
            "outcome": dict(self.outcome),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Refund:
    id: str
    created: int
    amount: int
    currency: str
    charge: str
    payment_intent: str
    reason: str  # requested_by_customer | duplicate | fraudulent
    metadata: Meta
    status: str = "succeeded"

    object_type = "refund"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "refund",
            "created": self.created,
            "amount": self.amount,
            "currency": self.currency,
            "charge": self.charge,
            "payment_intent": self.payment_intent,
            "reason": self.reason,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Subscription:
    id: str
    created: int
    customer: str
    status: str  # active | past_due | canceled
    price_id: str
    unit_amount: int
    currency: str
    current_period_start: int
    current_period_end: int
    default_payment_method: str
    metadata: Meta
    latest_invoice: str | None = None
    canceled_at: int | None = None

    object_type = "subscription"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "subscription",
            "created": self.created,
            "livemode": False,
            "customer": self.customer,
            "status": self.status,
            "collection_method": "charge_automatically",
            "current_period_start": self.current_period_start,
            "current_period_end": self.current_period_end,
            "default_payment_method": self.default_payment_method,
            "items": {
                "object": "list",
                "data": [
                    {
                        "object": "subscription_item",
                        "price": {
                            "id": self.price_id,
                            "object": "price",
                            "unit_amount": self.unit_amount,
                            "currency": self.currency,
                            "recurring": {"interval": "month", "interval_count": 1},
                        },
                        "quantity": 1,
                    }
                ],
            },
            "latest_invoice": self.latest_invoice,
            "canceled_at": self.canceled_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Invoice:
    id: str
    created: int
    customer: str
    subscription: str
    customer_country: str
    status: str  # draft | open | paid | uncollectible
    amount_due: int
    currency: str
    period_start: int
    period_end: int
    billing_reason: str  # subscription_cycle
    amount_paid: int = 0
    attempt_count: int = 0
    next_payment_attempt: int | None = None
    payment_intent: str | None = None
    metadata: Meta = field(default_factory=dict)

    object_type = "invoice"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "invoice",
            "created": self.created,
            "livemode": False,
            "customer": self.customer,
            "customer_address": {"country": self.customer_country},
            "subscription": self.subscription,
            "status": self.status,
            "collection_method": "charge_automatically",
            "billing_reason": self.billing_reason,
            "amount_due": self.amount_due,
            "amount_paid": self.amount_paid,
            "amount_remaining": self.amount_due - self.amount_paid,
            "currency": self.currency,
            "attempt_count": self.attempt_count,
            "attempted": self.attempt_count > 0,
            "next_payment_attempt": self.next_payment_attempt,
            "payment_intent": self.payment_intent,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Event:
    """Immutable event envelope. ``data_json`` is the frozen snapshot at emission time."""

    id: str
    created: int
    type: str
    object_type: str
    object_id: str
    data_json: str
    previous_attributes_json: str | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    api_version: str = API_VERSION
    delivered_at: int | None = None  # sim-1.2: set only for late-delivered events (data_pipeline_issue)

    def to_envelope(self) -> dict[str, Any]:
        data: dict[str, Any] = {"object": json.loads(self.data_json)}
        if self.previous_attributes_json is not None:
            data["previous_attributes"] = json.loads(self.previous_attributes_json)
        env = {
            "id": self.id,
            "object": "event",
            "api_version": self.api_version,
            "created": self.created,
            "livemode": False,
            "type": self.type,
            "data": data,
            "request": {"id": self.request_id, "idempotency_key": self.idempotency_key},
        }
        if self.delivered_at is not None:
            env["delivered_at"] = self.delivered_at
        return env

    def to_json_line(self) -> str:
        """Envelope JSON built by string assembly (hot path; equals canonical_json(to_envelope()))."""
        data = '{"object":' + self.data_json
        if self.previous_attributes_json is not None:
            data += ',"previous_attributes":' + self.previous_attributes_json
        data += "}"
        return (
            '{"id":' + json.dumps(self.id)
            + ',"object":"event","api_version":' + json.dumps(self.api_version)
            + ',"created":' + str(self.created)
            + ',"livemode":false,"type":' + json.dumps(self.type)
            + ',"data":' + data
            + ',"request":{"id":' + json.dumps(self.request_id)
            + ',"idempotency_key":' + json.dumps(self.idempotency_key) + "}"
            + ("" if self.delivered_at is None else ',"delivered_at":' + str(self.delivered_at))
            + "}"
        )


OBJECT_PREFIX = {
    "customer": "cus",
    "payment_method": "pm",
    "payment_intent": "pi",
    "charge": "ch",
    "refund": "re",
    "invoice": "in",
    "subscription": "sub",
    "event": "evt",
}
