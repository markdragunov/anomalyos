"""Normalization over a real (small, seeded) simulator run: coverage and consistency."""

from __future__ import annotations

import collections
import json

from anomalyos.events import normalize as nz
from anomalyos.events.store import norm_rows, run_id_of


def test_every_raw_type_is_mapped_or_intentionally_unmapped(full_run):
    seen = {e["type"] for e in full_run["events"]}
    assert seen <= (nz.MAPPED_RAW_TYPES | nz.INTENTIONALLY_UNMAPPED), seen - (nz.MAPPED_RAW_TYPES | nz.INTENTIONALLY_UNMAPPED)
    # nothing declared as mapped is dead code on the simulator's output
    assert nz.MAPPED_RAW_TYPES <= seen


def test_counts_are_consistent_with_the_raw_stream(full_run):
    rows = list(norm_rows(full_run["dir"], run_id_of(full_run["dir"])))
    raw = collections.Counter(e["type"] for e in full_run["events"])
    norm = collections.Counter(r["event_type"] for r in rows)
    assert norm["payment.authorized"] == norm["payment.captured"] == raw["charge.succeeded"]
    # fraud.flagged is emitted in addition to payment.declined, so every failed charge is exactly one decline or technical failure
    assert norm["payment.declined"] + norm["payment.failed"] == raw["charge.failed"]
    assert 0 < norm["fraud.flagged"] <= norm["payment.declined"]
    assert norm["refund.requested"] == raw["refund.created"]
    assert norm["invoice.created"] == raw["invoice.created"]
    assert norm["checkout.started"] + norm["subscription.renewal_attempted"] == raw["payment_intent.created"]
    assert norm["subscription.canceled"] == raw["customer.subscription.deleted"]
    assert norm["subscription.renewed"] + norm["subscription.past_due"] == raw["customer.subscription.updated"]


def test_normalization_is_deterministic_on_a_real_run(full_run):
    run_id = run_id_of(full_run["dir"])
    a = list(norm_rows(full_run["dir"], run_id))
    b = list(norm_rows(full_run["dir"], run_id))
    assert a == b and a


def test_no_empty_dimensions_and_every_row_traces_to_raw(full_run):
    ids = {e["id"] for e in full_run["events"]}
    for r in norm_rows(full_run["dir"], run_id_of(full_run["dir"])):
        assert r["raw_event_id"] in ids
        assert all(r[d] != "" for d in nz.DIMENSION_COLUMNS)
        assert set(r) == set(nz.NORM_COLUMN_NAMES)


def test_idempotency_signal_marks_only_keyless_checkouts_and_renewals(full_run):
    rows = list(norm_rows(full_run["dir"], run_id_of(full_run["dir"])))
    by_channel = collections.Counter((r["channel"], r["idempotency_key_present"]) for r in rows if r["event_type"] in ("checkout.started", "subscription.renewal_attempted"))
    assert by_channel[("renewal", 1)] == 0 and by_channel[("checkout", 1)] > 0
    keyless = [e for e in full_run["events"] if e["type"] == "payment_intent.created" and e["request"]["idempotency_key"] is None
               and e["data"]["object"]["metadata"]["channel"] == "checkout"]
    assert by_channel[("checkout", 0)] == len(keyless)


def test_json_serialisable_for_loading(full_run):
    r = next(norm_rows(full_run["dir"], run_id_of(full_run["dir"])))
    json.dumps(r)
