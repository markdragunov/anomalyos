"""Normalization: one golden test per mapping row (ADR-025), on hand-built events (not the simulator)."""

from __future__ import annotations

import copy

import pytest

from anomalyos.events.normalize import (
    EVENT_TYPES, INTENTIONALLY_UNMAPPED, MAPPED_RAW_TYPES, NORM_COLUMN_NAMES, DIMENSION_COLUMNS, Normalizer,
    NormalizationError, normalize,
)

T0 = 1_786_000_000
RUN = "run_0123456789abcdef"
META = {"channel": "checkout", "psp": "psp_alpha", "customer_country": "DE", "platform": "web", "app_version": "web"}


def env(eid, etype, obj, *, created=T0, prev=None, idem=None):
    return {"id": eid, "object": "event", "api_version": "x", "created": created, "type": etype,
            "data": {"object": obj, "previous_attributes": prev}, "request": {"id": None, "idempotency_key": idem}}


def pi(pid="pi_1", channel="checkout", invoice=None, created=T0):
    meta = dict(META, channel=channel)
    return env(f"evt_{pid}", "payment_intent.created",
               {"id": pid, "object": "payment_intent", "customer": "cus_1", "currency": "eur", "amount": 1000,
                "invoice": invoice, "metadata": meta}, created=created, idem="k-1" if channel == "checkout" else None)


def charge(cid, pid, ok=True, code=None, reason=None, brand="visa", created=T0 + 5, meta=None):
    obj = {"id": cid, "object": "charge", "customer": "cus_1", "payment_intent": pid, "currency": "eur", "amount": 1000,
           "payment_method_details": {"type": "card", "card": {"brand": brand, "country": "FR"}},
           "failure_code": code, "outcome": {"reason": reason}, "metadata": meta or dict(META)}
    return env(f"evt_{cid}", "charge.succeeded" if ok else "charge.failed", obj, created=created)


def run(*events):
    return list(normalize(list(enumerate(events)), RUN))


def types(rows):
    return [r["event_type"] for r in rows]


def test_checkout_started():
    rows = run(pi())
    assert types(rows) == ["checkout.started"]
    r = rows[0]
    assert (r["entity_id"], r["channel"], r["psp"], r["customer_country"], r["amount_minor"], r["currency"]) == ("pi_1", "checkout", "psp_alpha", "DE", 1000, "eur")
    assert r["idempotency_key_present"] == 1 and r["attempt_no"] == 0
    assert r["payment_method_type"] == r["card_brand"] == r["issuer_country"] == "unknown"  # explicit, never ''


def test_renewal_attempted_has_no_idempotency_key():
    rows = run(pi(channel="renewal"))
    assert types(rows) == ["subscription.renewal_attempted"] and rows[0]["idempotency_key_present"] == 0


def test_charge_succeeded_is_authorized_plus_captured():
    rows = run(pi(), charge("ch_1", "pi_1"))
    assert types(rows)[1:] == ["payment.authorized", "payment.captured"]
    for r in rows[1:]:
        assert (r["entity_id"], r["attempt_no"], r["card_brand"], r["issuer_country"], r["payment_method_type"]) == ("ch_1", 1, "visa", "FR", "card")
        assert r["idempotency_key_present"] == 1 and r["channel"] == "checkout" and r["payment_intent_id"] == "pi_1"


@pytest.mark.parametrize("code", ["card_declined", "expired_card", "incorrect_cvc", "payment_method_provider_decline"])
def test_issuer_failure_is_declined(code):
    rows = run(pi(), charge("ch_1", "pi_1", ok=False, code=code, reason="generic_decline"))
    assert types(rows)[1:] == ["payment.declined"]
    assert (rows[1]["error_code"], rows[1]["decline_code"], rows[1]["status"]) == (code, "generic_decline", "declined")


def test_processing_error_is_technical_failure():
    rows = run(pi(), charge("ch_1", "pi_1", ok=False, code="processing_error"))
    assert types(rows)[1:] == ["payment.failed"] and rows[1]["decline_code"] is None


def test_highest_risk_level_is_fraud_flag_plus_declined():
    rows = run(pi(), charge("ch_1", "pi_1", ok=False, code="card_declined", reason="highest_risk_level"))
    assert types(rows)[1:] == ["fraud.flagged", "payment.declined"]


def test_unknown_failure_code_fails_loudly():
    with pytest.raises(NormalizationError, match="unmapped failure_code"):
        run(pi(), charge("ch_1", "pi_1", ok=False, code="brand_new_code"))


def test_attempt_numbering_with_retries_and_dunning():
    rows = run(pi(channel="renewal"),
               charge("ch_1", "pi_1", ok=False, code="card_declined", reason="insufficient_funds", meta=dict(META, channel="renewal")),
               charge("ch_2", "pi_1", ok=False, code="card_declined", reason="insufficient_funds", meta=dict(META, channel="renewal")),
               charge("ch_3", "pi_1", ok=True, meta=dict(META, channel="renewal")))
    attempts = [(r["event_type"], r["attempt_no"]) for r in rows if r["event_type"].startswith("payment.") or r["event_type"] == "dunning.attempted"]
    assert attempts == [("payment.declined", 1), ("payment.declined", 2), ("dunning.attempted", 2),
                        ("payment.authorized", 3), ("payment.captured", 3), ("dunning.attempted", 3)]


def test_checkout_retry_is_not_dunning():
    rows = run(pi(), charge("ch_1", "pi_1", ok=False, code="card_declined", reason="do_not_honor"), charge("ch_2", "pi_1"))
    assert "dunning.attempted" not in types(rows)
    assert [r["attempt_no"] for r in rows if r["event_type"] == "payment.authorized"] == [2]


def test_charge_before_its_payment_intent_raises():
    with pytest.raises(NormalizationError, match="before its payment_intent.created"):
        run(charge("ch_1", "pi_missing"))


def sub_obj(status="active", period=False):
    return {"id": "sub_1", "object": "subscription", "customer": "cus_1", "status": status,
            "items": {"data": [{"price": {"unit_amount": 1199, "currency": "eur"}}]}, "metadata": {"customer_country": "DE", "plan": "monthly_eur"}}


def test_invoice_events_keep_names_and_carry_plan():
    sub_created = env("evt_s0", "customer.subscription.created", sub_obj())
    inv = {"id": "in_1", "object": "invoice", "customer": "cus_1", "subscription": "sub_1", "currency": "eur", "amount_due": 1199,
           "customer_address": {"country": "DE"}}
    rows = run(sub_created, env("evt_i1", "invoice.created", inv), env("evt_i2", "invoice.paid", inv), env("evt_i3", "invoice.payment_failed", inv))
    assert types(rows) == ["invoice.created", "invoice.paid", "invoice.payment_failed"]
    assert all(r["plan_id"] == "monthly_eur" and r["channel"] == "renewal" and r["entity_id"] == "in_1" for r in rows)


def test_plan_flows_to_renewal_payment_intent_and_charge():
    inv = {"id": "in_1", "object": "invoice", "customer": "cus_1", "subscription": "sub_1", "currency": "eur", "amount_due": 1199, "customer_address": {"country": "DE"}}
    rows = run(env("evt_s0", "customer.subscription.created", sub_obj()), env("evt_i1", "invoice.created", inv),
               pi(channel="renewal", invoice="in_1"), charge("ch_1", "pi_1", meta=dict(META, channel="renewal")))
    assert {r["plan_id"] for r in rows if r["event_type"] != "invoice.created"} == {"monthly_eur"}


def test_subscription_updated_period_advance_is_renewed():
    rows = run(env("evt_u", "customer.subscription.updated", sub_obj(), prev={"current_period_start": 1, "current_period_end": 2, "latest_invoice": None, "status": "active"}))
    assert types(rows) == ["subscription.renewed"] and rows[0]["plan_id"] == "monthly_eur" and rows[0]["amount_minor"] == 1199


def test_subscription_updated_past_due():
    rows = run(env("evt_u", "customer.subscription.updated", sub_obj("past_due"), prev={"latest_invoice": None, "status": "active"}))
    assert types(rows) == ["subscription.past_due"]


def test_subscription_updated_unknown_shape_fails_loudly():
    with pytest.raises(NormalizationError, match="unmapped shape"):
        run(env("evt_u", "customer.subscription.updated", sub_obj("active"), prev={"status": "trialing"}))


def test_subscription_deleted_is_canceled():
    assert types(run(env("evt_d", "customer.subscription.deleted", sub_obj("canceled")))) == ["subscription.canceled"]


def test_refund_created_is_requested_plus_succeeded():
    obj = {"id": "re_1", "object": "refund", "payment_intent": "pi_1", "currency": "eur", "amount": 500, "status": "succeeded", "metadata": dict(META)}
    rows = run(env("evt_r", "refund.created", obj))
    assert types(rows) == ["refund.requested", "refund.succeeded"] and rows[0]["entity_id"] == "re_1" and rows[0]["amount_minor"] == 500


@pytest.mark.parametrize("raw_type", sorted(INTENTIONALLY_UNMAPPED))
def test_intentionally_unmapped_types_produce_no_rows(raw_type):
    obj = {"id": "x_1", "object": "x", "customer": "cus_1", "metadata": {}}
    assert run(env("evt_x", raw_type, obj)) == []


def test_unknown_raw_type_fails_loudly():
    with pytest.raises(NormalizationError, match="unknown raw event type"):
        run(env("evt_x", "dispute.created", {"id": "dp_1", "object": "dispute"}))


def test_unmapped_and_mapped_sets_are_disjoint_and_vocabulary_is_closed():
    assert not (INTENTIONALLY_UNMAPPED & MAPPED_RAW_TYPES)
    assert "chargeback.opened" not in EVENT_TYPES  # in DATA_MODEL, not generated yet (ADR-025)


def test_rows_have_exact_columns_no_empty_dimensions_and_no_truth_identifiers():
    rows = run(pi(), charge("ch_1", "pi_1"), charge("ch_2", "pi_1", ok=False, code="processing_error"))
    for r in rows:
        assert tuple(r) == NORM_COLUMN_NAMES or set(r) == set(NORM_COLUMN_NAMES)
        assert all(r[d] != "" for d in DIMENSION_COLUMNS)
        blob = " ".join(str(v) for k, v in r.items() if k not in ("run_id",))
        assert "scn_" not in blob and "inc_" not in blob and "fx_" not in blob
    assert all(r["run_id"] == RUN for r in rows)  # the only run-level identifier, used for partitioning


def test_event_ids_are_deterministic_and_unique():
    rows = run(pi(), charge("ch_1", "pi_1", ok=False, code="card_declined", reason="highest_risk_level"))
    ids = [r["event_id"] for r in rows]
    assert len(ids) == len(set(ids)) and ids[1] == "evt_ch_1/fraud.flagged"


def test_deterministic_and_input_not_mutated():
    events = [pi(), charge("ch_1", "pi_1"), charge("ch_2", "pi_1", ok=False, code="card_declined", reason="do_not_honor")]
    snapshot = copy.deepcopy(events)
    assert run(*events) == run(*events)
    assert events == snapshot
    n1, n2 = Normalizer(RUN), Normalizer(RUN)  # independent instances give identical output
    assert [n1.feed(i, e) for i, e in enumerate(events)] == [n2.feed(i, e) for i, e in enumerate(events)]


def test_ingested_at_equals_occurred_at_never_wall_clock():
    rows = run(pi(created=T0 + 123))
    assert rows[0]["occurred_at"] == rows[0]["ingested_at"] == T0 + 123
