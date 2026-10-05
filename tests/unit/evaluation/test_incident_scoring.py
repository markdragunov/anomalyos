"""Stage 6 evaluation rules (ADR-041 D-9) on hand-built incidents, candidates and records."""

from __future__ import annotations

from pulseos.evaluation.incidents import label_ok, score_incidents

H, D = 3600, 86_400
W0, W1 = 0, 28 * D


def rec(key, iid, route="incident", start=5 * D, kind="psp_authorization_degradation", unrelated=(), metrics=("charge_approval_rate",),
        recovery=None, lost=None):
    return {"record_key": key, "incident_id": iid, "scenario_kind": kind, "expected_route": route, "start": start,
            "end": start + 6 * H, "expected_recovery": recovery, "oracle_detectable_at": start + H, "affected_cohorts": [{}],
            "affected_metrics": list(metrics), "unchanged_metrics": [], "unrelated_to": list(unrelated),
            "expected_impact": {"lost_successful_payments": lost} if lost else {}, "true_impact": {"lost_revenue_minor": {"eur": 1000}}}


def cand(cid, start=5 * D, metric="authorization_rate"):
    return {"anomaly_id": cid, "metric": metric, "scope": (), "window_start": start, "window_end": start + 2 * H,
            "detected_at": start + H, "status": "promoted"}


def inc(iid, members, detected_at=5 * D + H, impact=None):
    return {"incident_id": iid, "linked_anomalies": members, "detected_at": detected_at, "estimated_impact": impact}


def test_coverage_duplicates_purity_and_wrong_merges():
    recs = [rec("a", "inc_a", unrelated=("inc_b",)), rec("b", "inc_b", start=5 * D + 30 * 60, kind="correlated_unrelated_anomalies",
            unrelated=("inc_a",))]
    cands = [cand("c1"), cand("c2"), cand("c3", start=5 * D + 30 * 60)]
    # c1 and c2 both match a (earliest start wins ties), c3 matches a and b -> status picks a (earlier start)
    incidents = {"i1": inc("i1", ["c1"]), "i2": inc("i2", ["c2", "c3"])}
    out = score_incidents(incidents, [], {}, cands, recs, W0, W1)["summary"]
    assert out["coverage"] == 0.5  # a covered, b not (c3's precedence picked a)
    assert out["duplicates_per_covered"] == 1.0  # a is the main record of two engine incidents
    assert out["purity"] == 1.0 and out["wrong_merge_rate"] == 0.0 and out["incidents_per_day"] == 2 / 25


def test_wrong_and_campaign_merges_are_detected():
    recs = [rec("a", "inc_a", unrelated=("inc_b",)), rec("b", "inc_b", start=12 * D, kind="correlated_unrelated_anomalies",
            unrelated=("inc_a",))]
    cands = [cand("c1"), cand("c2", start=12 * D)]
    out = score_incidents({"i1": inc("i1", ["c1", "c2"])}, [], {}, cands, recs, W0, W1)["summary"]
    assert out["wrong_merge_rate"] == 1.0 and out["campaign_merges"] == 1 and out["purity"] == 0.5


def test_recovery_timeliness_impact_and_system_terminal_count():
    recs = [rec("a", "inc_a", recovery=5 * D + 8 * H, lost=200)]
    imp = {"lost_successful_payments": {"value": 180.0, "interval": [150.0, 230.0]},
           "lost_revenue_minor": {"per_currency": {"eur": {"value": 1500, "interval": [900, 2000]}}}}
    events = [{"incident_id": "i1", "seq": 1, "event_time": 5 * D + H, "status": "DETECTED", "actor": "policy"},
              {"incident_id": "i1", "seq": 2, "event_time": 5 * D + 10 * H, "status": "RECOVERING", "actor": "system"},
              {"incident_id": "i1", "seq": 3, "event_time": 5 * D + 11 * H, "status": "DISMISSED", "actor": "system"}]
    out = score_incidents({"i1": inc("i1", ["c1"], impact=imp)}, events, {}, [cand("c1")], recs, W0, W1)["summary"]
    assert out["recovery_within_6h"] == 1.0 and out["terminal_by_system"] == 1
    assert out["creation_delay_h_median"] == 0.0
    assert abs(out["lost_payments_median_rel_error"] - 0.1) < 1e-9 and out["lost_payments_interval_coverage"] == 1.0
    assert abs(out["lost_revenue_median_rel_error"] - 0.5) < 1e-9 and out["lost_revenue_interval_coverage"] == 1.0


def test_label_expectations():
    assert label_ok("rate_change", rec("a", "x")) is True and label_ok("mix_shift", rec("a", "x")) is False
    assert label_ok("mixed", rec("f", "x", kind="fraud_like_spike")) is True
    assert label_ok("new_cohort", rec("n", "x", kind="checkout_regression_app_version")) is True
    assert label_ok("rate_change", rec("s", None, route="suppress")) is None and label_ok("rate_change", None) is None
