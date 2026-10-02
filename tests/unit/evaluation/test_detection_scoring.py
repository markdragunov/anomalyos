"""Each evaluation rule of ADR-034 D-7 on hand-built records and candidates."""

from __future__ import annotations

from anomalyos.evaluation.detection import score

W0, W1 = 0, 28 * 86_400
H = 3600


def rec(key, route="incident", start=5 * 86_400, end=5 * 86_400 + 4 * H, oracle=5 * 86_400 + H, cohorts=({"psp": ["psp_beta"]},),
        affected=("charge_approval_rate",), unchanged=(), kind="psp_authorization_degradation", recovery=None):
    return {"record_key": key, "scenario_kind": kind, "expected_route": route, "start": start, "end": end,
            "expected_recovery": recovery, "oracle_detectable_at": oracle if route == "incident" else None,
            "affected_cohorts": list(cohorts), "affected_metrics": list(affected), "unchanged_metrics": list(unchanged)}


def cand(cid, metric="authorization_rate", scope=(("psp", "psp_beta"),), ws=5 * 86_400 + H, we=5 * 86_400 + 3 * H,
         detected=5 * 86_400 + 2 * H, status="promoted"):
    return {"anomaly_id": cid, "series_key": "S2", "metric": metric, "scope": scope, "window_start": ws, "window_end": we,
            "detected_at": detected, "status": status}


def test_match_counts_recall_and_latency_from_the_oracle():
    out = score([cand("a")], [rec("r1")], W0, W1)
    s = out["summary"]
    assert s["recall"] == 1.0 and s["false_positives"] == 0
    assert out["records"][0]["latency_s"] == H  # detected at start + 2 h, oracle at start + 1 h


def test_scope_must_be_compatible():
    out = score([cand("a", scope=(("psp", "psp_alpha"),))], [rec("r1")], W0, W1)
    assert out["summary"]["recall"] == 0.0 and out["candidates"][0]["class"] == "fp_unmatched"
    assert score([cand("g", scope=())], [rec("r1")], W0, W1)["summary"]["recall"] == 1.0  # global scope always fits


def test_metric_must_be_in_the_affected_family_and_unchanged_metrics_are_false_positives():
    r = rec("r1", affected=("payment_intent_conversion_rate",), unchanged=("charge_approval_rate",),
            cohorts=({"platform": ["android"]},))
    out = score([cand("a", scope=())], [r], W0, W1)
    assert out["summary"]["recall"] == 0.0 and out["candidates"][0]["class"] == "fp_unchanged_metric"
    ok = score([cand("b", metric="checkout_conversion_rate", scope=())], [r], W0, W1)
    assert ok["summary"]["recall"] == 1.0


def test_suppress_matches_are_false_positives_and_watch_is_reported_only():
    sup = rec("s1", route="suppress", kind="harmless_seasonality")
    out = score([cand("a")], [sup], W0, W1)
    assert out["candidates"][0]["class"] == "fp_suppress" and out["candidates"][0]["detail"] == ["harmless_seasonality"]
    assert out["summary"]["incidents_scored"] == 0
    watch = rec("w1", route="watch", kind="ambiguous_signal")
    out = score([cand("b")], [watch], W0, W1)
    assert out["candidates"][0]["class"] == "watch" and out["summary"]["false_positives"] == 0


def test_oracle_null_incidents_are_excluded_from_recall():
    out = score([], [rec("r1", oracle=None) | {"oracle_detectable_at": None}], W0, W1)
    assert out["summary"]["incidents_scored"] == 0 and out["summary"]["incidents_oracle_null"] == 1


def test_miss_reasons_separate_the_warm_up():
    early = rec("e", start=2 * 86_400, end=2 * 86_400 + 3 * H, oracle=2 * 86_400 + H)
    late = rec("l")
    out = score([], [early, late], W0, W1)
    reasons = {r["record_key"]: r["outcome"] for r in out["records"]}
    assert reasons == {"e": "warm_up", "l": "missed"} and out["summary"]["recall"] == 0.0


def test_time_window_includes_expected_recovery_and_open_ended_records():
    r = rec("r", end=5 * 86_400 + 2 * H, recovery=5 * 86_400 + 6 * H)
    late_cand = cand("a", ws=5 * 86_400 + 4 * H, we=5 * 86_400 + 5 * H, detected=5 * 86_400 + 5 * H)
    assert score([late_cand], [r], W0, W1)["summary"]["recall"] == 1.0
    open_ended = rec("o", end=None)
    assert score([cand("b", ws=20 * 86_400, we=21 * 86_400, detected=21 * 86_400)], [open_ended], W0, W1)["summary"]["recall"] == 1.0


def test_suppressed_candidates_are_ignored_and_extra_matches_are_not_false_positives():
    out = score([cand("a"), cand("b", detected=5 * 86_400 + 3 * H), cand("c", status="suppressed")], [rec("r1")], W0, W1)
    assert out["summary"]["candidates"] == 2 and out["summary"]["false_positives"] == 0
    assert out["records"][0]["n_candidates"] == 2
