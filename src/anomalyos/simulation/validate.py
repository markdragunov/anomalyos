"""Structural validation of a generated run: events, object relationships, credentials, truth.

Why: acceptance requires valid event/object relationships, machine-readable ground truth for
every scenario, and *no* sensitive payment credentials. This module checks those properties
on the emitted stream itself (not on generator internals), so it also guards later refactors.
The same relationship checks exist as SQL in ``clickhouse_load.SQL_CHECKS`` for loaded data.

Input:  event envelopes (dicts, in stream order) and ``GroundTruth`` dicts.
Output: ``Report`` with error list (capped) and counters; ``ok`` iff no errors.
Invariants: read-only; O(#objects) memory for the id registry.
Failure modes: never raises on bad data — every problem becomes an error entry.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .ground_truth import RootCause, Route
from .models import API_VERSION, OBJECT_PREFIX
from .scenarios import SELECTOR_DIMS
from .world import CARD_BRANDS, MARKETS, PAYMENT_METHOD_TYPES, PSPS

EVENT_OBJECT = {
    "customer.created": "customer",
    "payment_method.attached": "payment_method",
    "customer.subscription.created": "subscription",
    "customer.subscription.updated": "subscription",
    "customer.subscription.deleted": "subscription",
    "invoice.created": "invoice",
    "invoice.finalized": "invoice",
    "invoice.paid": "invoice",
    "invoice.payment_failed": "invoice",
    "invoice.marked_uncollectible": "invoice",
    "payment_intent.created": "payment_intent",
    "payment_intent.requires_action": "payment_intent",
    "payment_intent.succeeded": "payment_intent",
    "payment_intent.payment_failed": "payment_intent",
    "payment_intent.canceled": "payment_intent",
    "charge.succeeded": "charge",
    "charge.failed": "charge",
    "charge.refunded": "charge",
    "refund.created": "refund",
}
CREATION_EVENTS = {"customer.created", "payment_method.attached", "customer.subscription.created", "invoice.created",
                   "payment_intent.created", "charge.succeeded", "charge.failed", "refund.created"}
EXPECTED_STATUS = {
    "payment_intent.succeeded": "succeeded", "payment_intent.canceled": "canceled",
    "payment_intent.payment_failed": "requires_payment_method", "payment_intent.requires_action": "requires_action",
    "charge.succeeded": "succeeded", "charge.failed": "failed", "invoice.paid": "paid",
    "invoice.marked_uncollectible": "uncollectible", "customer.subscription.deleted": "canceled",
    "invoice.finalized": "open", "invoice.created": "draft",
}
TERMINAL_PI = {"succeeded", "canceled"}

# Keys that would hold credentials or direct identifiers. None may appear anywhere.
FORBIDDEN_KEYS = frozenset({
    "number", "card_number", "pan", "cvc", "cvv", "cvc_check", "exp_month", "exp_year", "last4", "dynamic_last4",
    "iban", "account_number", "routing_number", "sort_code", "bsb_number", "bank_account", "track_data", "pin",
    "password", "secret", "client_secret", "api_key", "email", "phone", "name", "tax_id",
})
_DIGITS = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
_IBAN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")
_LEAK = ("scn_", "inc_", "fx_", "scenario", "incident", "ground_truth")
MAX_ERRORS = 200


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def scan_sensitive(obj: Any, path: str = "$") -> list[str]:
    """Return findings for forbidden keys, PAN-like numbers (Luhn-valid 13–19 digits) or IBANs."""
    out: list[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if k.lower() in FORBIDDEN_KEYS:
                out.append(f"{path}.{k}: forbidden key")
            out.extend(scan_sensitive(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(scan_sensitive(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        for m in _DIGITS.finditer(obj):
            if luhn_ok(m.group()):
                out.append(f"{path}: PAN-like digit string")
        if _IBAN.fullmatch(obj):
            out.append(f"{path}: IBAN-like string")
    elif isinstance(obj, int) and not isinstance(obj, bool) and obj >= 10**12:
        if luhn_ok(str(obj)) and 13 <= len(str(obj)) <= 19:
            out.append(f"{path}: PAN-like integer")
    return out


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    error_count: int = 0
    events: int = 0
    objects: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def err(self, msg: str) -> None:
        self.error_count += 1
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(msg)


class EventValidator:
    """Streaming validator; feed envelopes in stream order, then call ``finish()``."""

    def __init__(self, *, scan_every: int = 1) -> None:
        self.r = Report()
        self.seen: dict[str, set[str]] = {k: set() for k in OBJECT_PREFIX}
        self.event_ids: set[str] = set()
        self.last_created = -1
        self.pi_amount: dict[str, int] = {}
        self.pi_status: dict[str, str] = {}
        self.charge_amount: dict[str, int] = {}
        self.refunded: dict[str, int] = {}
        self.scan_every = max(1, scan_every)

    def _ref(self, kind: str, value: Any, ctx: str) -> None:
        if value is None:
            return
        if not isinstance(value, str) or value not in self.seen[kind]:
            self.r.err(f"{ctx}: dangling {kind} reference {value!r}")

    def feed(self, ev: Mapping[str, Any], raw_line: str | None = None) -> None:
        r = self.r
        r.events += 1
        eid, etype, created = ev.get("id"), ev.get("type"), ev.get("created")
        ctx = f"{eid} {etype}"
        if not isinstance(eid, str) or not eid.startswith("evt_"):
            r.err(f"{ctx}: bad event id")
        elif eid in self.event_ids:
            r.err(f"{ctx}: duplicate event id")
        else:
            self.event_ids.add(eid)
        if ev.get("object") != "event" or ev.get("api_version") != API_VERSION:
            r.err(f"{ctx}: bad envelope")
        req = ev.get("request")
        if not isinstance(req, Mapping) or set(req) != {"id", "idempotency_key"}:
            r.err(f"{ctx}: bad request block")
        if not isinstance(created, int) or created < self.last_created:
            r.err(f"{ctx}: created not monotone ({created} < {self.last_created})")
        else:
            self.last_created = created
        obj = (ev.get("data") or {}).get("object")
        kind = EVENT_OBJECT.get(etype)
        if kind is None or not isinstance(obj, Mapping):
            r.err(f"{ctx}: unknown event type or missing data.object")
            return
        oid = obj.get("id", "")
        if obj.get("object") != kind or not str(oid).startswith(OBJECT_PREFIX[kind] + "_"):
            r.err(f"{ctx}: object/type mismatch ({obj.get('object')}, {oid})")
            return
        if obj.get("created", 0) > created:
            r.err(f"{ctx}: object created after the event announcing it")
        want = EXPECTED_STATUS.get(etype)
        if want and obj.get("status") != want:
            r.err(f"{ctx}: status {obj.get('status')!r} != {want!r}")
        if r.events % self.scan_every == 0:
            leak_src = raw_line if raw_line is not None else json.dumps(ev)
            for token in _LEAK:
                if token in leak_src:
                    r.err(f"{ctx}: ground-truth token {token!r} leaked into event")
            for finding in scan_sensitive(ev):
                r.err(f"{ctx}: {finding}")

        first = oid not in self.seen[kind]
        if first and etype not in CREATION_EVENTS:
            r.err(f"{ctx}: first event for {oid} is not a creation event")
        if not first and etype in CREATION_EVENTS and etype not in ("charge.succeeded", "charge.failed"):
            r.err(f"{ctx}: object {oid} created twice")

        getattr(self, "_" + kind)(etype, obj, ctx)
        self.seen[kind].add(oid)

    # per-object relationship rules -------------------------------------------------------
    def _customer(self, etype, o, ctx):
        pass

    def _payment_method(self, etype, o, ctx):
        self._ref("customer", o.get("customer"), ctx)
        if o.get("type") not in PAYMENT_METHOD_TYPES:
            self.r.err(f"{ctx}: unknown payment method type")

    def _subscription(self, etype, o, ctx):
        self._ref("customer", o.get("customer"), ctx)
        self._ref("payment_method", o.get("default_payment_method"), ctx)
        self._ref("invoice", o.get("latest_invoice"), ctx)
        if o.get("current_period_end", 0) <= o.get("current_period_start", 0):
            self.r.err(f"{ctx}: empty subscription period")

    def _invoice(self, etype, o, ctx):
        self._ref("customer", o.get("customer"), ctx)
        self._ref("subscription", o.get("subscription"), ctx)
        self._ref("payment_intent", o.get("payment_intent"), ctx)
        if etype == "invoice.paid" and o.get("amount_paid") != o.get("amount_due"):
            self.r.err(f"{ctx}: paid invoice amount mismatch")

    def _payment_intent(self, etype, o, ctx):
        self._ref("customer", o.get("customer"), ctx)
        self._ref("payment_method", o.get("payment_method"), ctx)
        self._ref("invoice", o.get("invoice"), ctx)
        self._ref("charge", o.get("latest_charge"), ctx)
        pid = o["id"]
        if not isinstance(o.get("amount"), int) or o["amount"] <= 0:
            self.r.err(f"{ctx}: non-positive amount")
        prev = self.pi_status.get(pid)
        if prev in TERMINAL_PI:
            self.r.err(f"{ctx}: event after terminal status {prev}")
        if pid in self.pi_amount and self.pi_amount[pid] != o.get("amount"):
            self.r.err(f"{ctx}: PaymentIntent amount changed")
        self.pi_amount[pid] = o.get("amount")
        self.pi_status[pid] = o.get("status")
        if etype == "payment_intent.succeeded" and o.get("amount_received") != o.get("amount"):
            self.r.err(f"{ctx}: amount_received != amount")

    def _charge(self, etype, o, ctx):
        self._ref("customer", o.get("customer"), ctx)
        self._ref("payment_intent", o.get("payment_intent"), ctx)
        self._ref("payment_method", o.get("payment_method"), ctx)
        self._ref("invoice", o.get("invoice"), ctx)
        cid, pid = o["id"], o.get("payment_intent")
        if pid in self.pi_amount and o.get("amount") != self.pi_amount[pid]:
            self.r.err(f"{ctx}: charge amount != PaymentIntent amount")
        if self.pi_status.get(pid) in TERMINAL_PI and etype != "charge.refunded":
            self.r.err(f"{ctx}: charge on a terminal PaymentIntent")
        if etype == "charge.failed" and not o.get("failure_code"):
            self.r.err(f"{ctx}: failed charge without failure_code")
        if etype == "charge.refunded":
            if o.get("amount_refunded", 0) > o.get("amount", 0) or o.get("amount_refunded") != self.refunded.get(cid):
                self.r.err(f"{ctx}: amount_refunded inconsistent with refunds")
        else:
            self.charge_amount[cid] = o.get("amount")

    def _refund(self, etype, o, ctx):
        self._ref("charge", o.get("charge"), ctx)
        self._ref("payment_intent", o.get("payment_intent"), ctx)
        ch = o.get("charge")
        total = self.refunded.get(ch, 0) + o.get("amount", 0)
        if ch in self.charge_amount and total > self.charge_amount[ch]:
            self.r.err(f"{ctx}: refunds exceed charge amount")
        self.refunded[ch] = total

    def finish(self) -> Report:
        self.r.objects = {k: len(v) for k, v in self.seen.items()}
        return self.r


_ALLOWED_VALUES = {
    "customer_country": {m.country for m in MARKETS}, "psp": set(PSPS), "payment_method_type": set(PAYMENT_METHOD_TYPES),
    "card_brand": set(CARD_BRANDS), "platform": {"web", "ios", "android"}, "channel": {"checkout", "renewal"},
}


def validate_ground_truth(records: Iterable[Mapping[str, Any]], scenario_ids: Iterable[str], *,
                          min_attempts_for_direction: int = 200) -> Report:
    r = Report()
    records = list(records)
    by_scn: dict[str, int] = {}
    inc_ids: set[str] = set()
    keys = {f"{g['scenario_id']}:{g['record_key']}" for g in records}
    for g in records:
        ctx = f"{g.get('scenario_id')}:{g.get('record_key')}"
        by_scn[g["scenario_id"]] = by_scn.get(g["scenario_id"], 0) + 1
        for fld in ("scenario_id", "incident_id", "start", "end", "true_cause", "root_cause", "expected_route",
                    "affected_cohorts", "control_cohorts", "expected_metric_effect", "expected_impact",
                    "expected_detection_window", "expected_recovery", "scenario_type", "seed", "is_incident",
                    "onset_at", "end_at", "ramp", "affected_metrics", "injected_effect", "true_impact"):
            if fld not in g:
                r.err(f"{ctx}: missing field {fld}")
        if g.get("true_cause") not in {c.value for c in RootCause}:
            r.err(f"{ctx}: true_cause outside vocabulary")
        if g.get("root_cause", {}).get("cause") != g.get("true_cause"):
            r.err(f"{ctx}: root_cause.cause != true_cause")
        route = g.get("expected_route")
        if route not in {x.value for x in Route}:
            r.err(f"{ctx}: route outside vocabulary")
        incident = route != Route.SUPPRESS.value
        if g.get("is_incident") is not incident:
            r.err(f"{ctx}: is_incident inconsistent with expected_route")
        if incident:
            iid = g.get("incident_id")
            if not (isinstance(iid, str) and iid.startswith("inc_")):
                r.err(f"{ctx}: incident without incident_id")
            elif iid in inc_ids:
                r.err(f"{ctx}: duplicate incident_id")
            else:
                inc_ids.add(iid)
            dw = g.get("expected_detection_window")
            if not dw or dw["end"] <= dw["start"] or dw["start"] != g["start"]:
                r.err(f"{ctx}: bad detection window")
        else:
            if g.get("incident_id") is not None or g.get("expected_detection_window") is not None:
                r.err(f"{ctx}: suppressed signal must not carry incident_id / detection window")
        if g.get("end") is not None and g["end"] < g["start"]:
            r.err(f"{ctx}: end before start")
        if g.get("expected_recovery") is not None and g["expected_recovery"] < g["start"]:
            r.err(f"{ctx}: recovery before start")
        aff = [json.dumps(c, sort_keys=True) for c in g.get("affected_cohorts", [])]
        ctl = [json.dumps(c, sort_keys=True) for c in g.get("control_cohorts", [])]
        if set(aff) & set(ctl):
            r.err(f"{ctx}: affected and control cohorts overlap")
        for cohort in g.get("affected_cohorts", []) + g.get("control_cohorts", []):
            for dim, vals in cohort.items():
                if dim not in SELECTOR_DIMS:
                    r.err(f"{ctx}: unknown cohort dimension {dim}")
                elif dim in _ALLOWED_VALUES and not set(vals) <= _ALLOWED_VALUES[dim]:
                    r.err(f"{ctx}: unknown values {vals} for {dim}")
        for u in g.get("unrelated_to", []):
            if not (u.startswith("inc_") or u in keys):
                r.err(f"{ctx}: unrelated_to points at unknown record {u}")
        # Measured effect must agree with the declared direction when the sample is meaningful.
        meas = g.get("expected_metric_effect", {}).get("measured", {})
        metric = g.get("expected_metric_effect", {}).get("primary_metric")
        n = meas.get("charge_attempts") or 0
        if incident and n >= min_attempts_for_direction:
            a, cf = meas.get("charge_approval_rate_actual"), meas.get("charge_approval_rate_counterfactual")
            if metric == "charge_approval_rate" and not (a is not None and cf is not None and a < cf):
                r.err(f"{ctx}: approval did not drop vs counterfactual")
            if metric == "payment_intent_conversion_rate" and not (meas["pi_conversion_actual"] < meas["pi_conversion_counterfactual"]):
                r.err(f"{ctx}: conversion did not drop vs counterfactual")
            if metric == "renewal_success_rate" and not (meas["renewal_first_attempt_success_actual"] < meas["renewal_first_attempt_success_counterfactual"]):
                r.err(f"{ctx}: renewal success did not drop")
            if metric == "refund_rate" and not meas.get("extra_refunds"):
                r.err(f"{ctx}: no extra refunds measured")
            if metric == "duplicate_charge_rate" and not meas.get("duplicate_charges"):
                r.err(f"{ctx}: no duplicate charges measured")
            if metric == "charge_attempt_volume" and not meas.get("injected_attempts"):
                r.err(f"{ctx}: no injected attempts measured")
    for sid in scenario_ids:
        if not by_scn.get(sid):
            r.err(f"{sid}: scenario has no ground-truth record")
    return r
