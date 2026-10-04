"""Stage 5 evaluation rules (ADR-039 D-9) on hand-built candidates and records."""

from __future__ import annotations

from anomalyos.evaluation.decisions import score_decisions, status

H, D = 3600, 86_400
W0, W1 = 0, 28 * D


def rec(key, route, start=5 * D, metrics=("charge_approval_rate",), severity="high", cause="psp_degradation", oracle=True):
    return {"record_key": key, "scenario_kind": "k", "expected_route": route, "start": start, "end": start + 6 * H,
            "expected_recovery": None, "oracle_detectable_at": start + H if oracle else None,
            "affected_cohorts": [{}], "affected_metrics": list(metrics), "unchanged_metrics": [],
            "severity": severity, "true_cause": cause}


def cand(cid, start=5 * D, metric="authorization_rate"):
    return {"anomaly_id": cid, "metric": metric, "scope": (), "window_start": start, "window_end": start + 2 * H,
            "detected_at": start + H, "status": "promoted"}


def answers(p, sev="high", cause="psp_degradation"):
    return {"is_incident": {"p": p}, "needs_human": {"p": 0.1},
            "severity": {"probs": {s: (1.0 if s == sev else 0.0) for s in ("low", "medium", "high", "critical")}, "confidence": 0.8},
            "category": {"probs": {cause: 0.7, "unknown": 0.2, "normal_variation": 0.1}, "confidence": 0.6}}


def test_status_precedence_and_unmatched():
    rs = [rec("s", "suppress"), rec("w", "watch"), rec("i", "incident")]
    assert status(cand("a"), rs, W1)[0] == "incident"
    assert status(cand("a"), rs[:2], W1)[0] == "watch"
    assert status(cand("a", start=20 * D), rs, W1) == ("unmatched", None)


def test_route_metrics_recall_and_calibration():
    rs = [rec("i1", "incident"), rec("s1", "suppress", start=10 * D)]
    rows = [
        {"candidate": cand("a"), "routes": {"jev": "INCIDENT", "baseline": "DIGEST"}, "verified": True, "reasons": (), "answers": answers(0.9)},
        {"candidate": cand("b"), "routes": {"jev": "INCIDENT", "baseline": "INCIDENT"}, "verified": True, "reasons": (), "answers": answers(0.8, sev="low")},
        {"candidate": cand("c", start=10 * D), "routes": {"jev": "INCIDENT", "baseline": "IGNORE"}, "verified": True, "reasons": (), "answers": answers(0.2)},
        {"candidate": cand("d", start=20 * D), "routes": {"jev": "DIGEST", "baseline": "DIGEST"}, "verified": False, "reasons": ("timeout",), "answers": {}},
    ]
    out = score_decisions(rows, rs, W0, W1)
    j, b = out["systems"]["jev"], out["systems"]["baseline"]
    assert j["incident_recall_at_incident"] == 1.0 and j["incident_routes_per_matched_incident"] == 2.0
    assert b["incident_recall_at_incident"] == 1.0 and b["route_accuracy_matched"] == 2 / 3
    assert j["route_accuracy_matched"] == 2 / 3 and j["suppress_incident_per_day"] == 1 / 25
    a = out["answers"]
    assert a["verified_share"] == 0.75 and a["failure_reasons"] == {"timeout": 1}
    assert a["severity_accuracy"] == 0.5 and a["category_top1"] == 1.0
    assert abs(a["brier"] - ((0.9 - 1) ** 2 + (0.8 - 1) ** 2 + 0.2 ** 2) / 3) < 1e-12
