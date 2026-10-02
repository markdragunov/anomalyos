"""Acceptance: valid event/object relationships, no credentials, machine-readable truth."""

from __future__ import annotations

import copy
import json

import pytest

from anomalyos.simulation.validate import EventValidator, luhn_ok, scan_sensitive, validate_ground_truth


def _validate(events, lines=None):
    v = EventValidator()
    for i, ev in enumerate(events):
        v.feed(ev, lines[i] if lines else None)
    return v.finish()


def test_generated_stream_is_valid(full_run):
    rep = _validate(full_run["events"], full_run["lines"])
    assert rep.ok, rep.errors[:10]
    assert rep.events == full_run["res"].events
    assert rep.objects["payment_intent"] > 10_000 and rep.objects["refund"] > 0 and rep.objects["invoice"] > 0


def test_every_scenario_has_valid_machine_readable_truth(full_run):
    res = full_run["res"]
    truth = json.loads((full_run["dir"] / "ground_truth.json").read_text())["records"]
    scn = [s["scenario_id"] for s in res.manifest["scenarios"]]
    rep = validate_ground_truth(truth, scn, min_attempts_for_direction=50)
    assert rep.ok, rep.errors
    assert {g["scenario_id"] for g in truth} == set(scn)
    assert len(scn) == 14  # 13 required kinds + negative control


def test_envelope_shape_matches_contract(full_run):
    ev = full_run["events"][-1]
    assert set(ev) == {"id", "object", "api_version", "created", "livemode", "type", "data", "request"}
    assert ev["id"].startswith("evt_") and ev["object"] == "event" and ev["api_version"].startswith("2026-")
    assert set(ev["request"]) == {"id", "idempotency_key"}
    assert "id" in ev["data"]["object"]


# --- the validator must actually catch problems -------------------------------------------------

def _first(events, etype):
    return next(i for i, e in enumerate(events) if e["type"] == etype)


@pytest.fixture()
def sample(full_run):
    return copy.deepcopy(full_run["events"][:20_000])


def test_detects_pan_and_forbidden_keys(sample):
    i = _first(sample, "payment_method.attached")
    sample[i]["data"]["object"]["card"] = {"brand": "visa", "number": "4242424242424242"}
    rep = _validate(sample)
    assert any("forbidden key" in e for e in rep.errors)
    assert any("PAN-like" in e for e in rep.errors)


def test_detects_dangling_reference(sample):
    i = _first(sample, "payment_intent.created")
    sample[i]["data"]["object"]["customer"] = "cus_doesnotexist0"
    assert any("dangling customer" in e for e in _validate(sample).errors)


def test_detects_truth_leak_into_events(sample):
    i = _first(sample, "charge.failed")
    sample[i]["data"]["object"]["metadata"]["note"] = "scn_psp_auth_degradation"
    assert any("leaked" in e for e in _validate(sample).errors)


def test_detects_time_travel_and_duplicate_ids(sample):
    sample[5000]["created"] = sample[4999]["created"] - 10
    sample[6001]["id"] = sample[6000]["id"]
    errs = _validate(sample).errors
    assert any("not monotone" in e for e in errs) and any("duplicate event id" in e for e in errs)


def test_detects_charge_amount_mismatch(sample):
    i = _first(sample, "charge.succeeded")
    sample[i]["data"]["object"]["amount"] += 1
    assert any("charge amount != PaymentIntent amount" in e for e in _validate(sample).errors)


def test_scan_sensitive_basics():
    assert luhn_ok("4242424242424242") and not luhn_ok("4242424242424241")
    assert scan_sensitive({"a": {"iban": "x"}})
    assert scan_sensitive({"a": "DE89370400440532013000"})
    assert not scan_sensitive({"created": 1760000000, "id": "pi_3Kq9aZ"})


def test_truth_validator_rejects_bad_records(full_run):
    truth = json.loads((full_run["dir"] / "ground_truth.json").read_text())["records"]
    bad = copy.deepcopy(truth)
    bad[1]["true_cause"] = "vibes"
    bad[2]["control_cohorts"] = bad[2]["affected_cohorts"]
    bad[3]["incident_id"] = None
    errs = validate_ground_truth(bad, [g["scenario_id"] for g in truth] + ["scn_missing"]).errors
    assert any("outside vocabulary" in e for e in errs)
    assert any("overlap" in e for e in errs)
    assert any("without incident_id" in e for e in errs)
    assert any("scn_missing" in e for e in errs)
