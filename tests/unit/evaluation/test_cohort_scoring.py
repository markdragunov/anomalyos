"""Stage 4 evaluation rules (ADR-037 C-10) on hand-built analyses."""

from __future__ import annotations

from anomalyos.evaluation.cohorts import localization, score_cohorts

H = 3600
W1 = 28 * 86_400


def test_localization_classes():
    truth = {"psp": ["psp_beta"]}
    assert localization([("psp", "psp_beta")], truth) == "exact"
    assert localization([("psp", "psp_beta"), ("customer_country", "DE")], truth) == "over_specific"
    assert localization([], truth) == "coarse"
    assert localization([("psp", "psp_alpha")], truth) == "wrong"
    assert localization([("customer_country", "DE")], truth) == "wrong"
    assert localization(None, truth) == "wrong"
    assert localization([("platform", "android")], {"platform": ["android"], "app_version": ["5.14.0"]}) == "coarse"


def _rec(**kw):
    base = {"record_key": "r", "scenario_kind": "psp_authorization_degradation", "expected_route": "incident",
            "start": 5 * 86_400, "end": 5 * 86_400 + 4 * H, "expected_recovery": None, "oracle_detectable_at": 5 * 86_400 + H,
            "affected_cohorts": [{"psp": ["psp_beta"]}], "affected_metrics": ["charge_approval_rate"], "unchanged_metrics": [],
            "root_cause": {"locus": {"psp": ["psp_beta"]}}, "expected_impact": {"lost_successful_payments": 100}}
    base.update(kw)
    return base


def _cand(cid="a", scope=()):
    return {"anomaly_id": cid, "metric": "authorization_rate", "scope": scope, "window_start": 5 * 86_400 + H,
            "window_end": 5 * 86_400 + 3 * H, "detected_at": 5 * 86_400 + 2 * H, "status": "promoted"}


def _analysis(cid="a", locus=(("psp", "psp_beta"),), label="rate_change", naive=(("psp", "psp_beta"), ("customer_country", "DE")),
              impact=None):
    return {"candidate_id": cid, "metric": "authorization_rate", "kind": "rate", "locus": locus, "naive_locus": naive,
            "label": label, "top": [{"dims": list(locus)}], "impact": impact}


def test_summary_counts_localization_label_naive_and_impact():
    imp = {"measure": "lost_successes", "value": 90.0, "interval": [70.0, 110.0], "epistemic": "estimated"}
    out = score_cohorts([_analysis(impact=imp)], [_cand()], [_rec()], W1)
    s = out["summary"]
    assert s["top1_exact"] == 1.0 and s["naive_top1_exact"] == 0.0 and s["label_accuracy_incidents"] == 1.0
    assert abs(s["impact_median_rel_error"] - 0.1) < 1e-9 and s["impact_interval_coverage"] == 1.0


def test_mix_records_expect_composition_labels():
    rec = _rec(expected_route="suppress", scenario_kind="harmless_seasonality", oracle_detectable_at=None,
               affected_metrics=["global_charge_approval_rate"], root_cause={"locus": {"customer_country": ["BR"]}})
    ok = score_cohorts([_analysis(label="mix_shift", locus=(("customer_country", "BR"),))], [_cand()], [rec], W1)
    bad = score_cohorts([_analysis(label="rate_change", locus=(("customer_country", "BR"),))], [_cand()], [rec], W1)
    assert ok["summary"]["label_accuracy_mix_records"] == 1.0 and bad["summary"]["label_accuracy_mix_records"] == 0.0


def test_first_matching_candidate_is_scored_and_others_count_for_any_exact():
    early = _cand("early")
    late = dict(_cand("late"), detected_at=5 * 86_400 + 3 * H)
    out = score_cohorts([_analysis("early", locus=()), _analysis("late")], [late, early], [_rec()], W1)
    row = out["records"][0]
    assert row["localization"] == "coarse" and row["any_candidate_exact"] is True


def test_equivalence_aware_localization_is_reported_beside_the_literal_one():
    from anomalyos.evaluation.cohorts import equivalent_exact
    dup = _rec(root_cause={"locus": {"platform": ["web"]}}, affected_cohorts=[{"platform": ["web"]}])
    assert equivalent_exact([("app_version", "web")], dup, "duplicate_charge_rate")
    ren = _rec(root_cause={"locus": {"channel": ["renewal"], "psp": ["psp_beta"]}},
               affected_cohorts=[{"channel": ["renewal"], "payment_method_type": ["card"], "psp": ["psp_beta"]}])
    assert equivalent_exact([("psp", "psp_beta"), ("payment_method_type", "card")], ren, "dunning_recovery_rate")
    assert equivalent_exact([("psp", "psp_beta")], ren, "dunning_recovery_rate")
    assert not equivalent_exact([("psp", "psp_beta")], ren, "authorization_rate")  # channel not implied here
    assert not equivalent_exact([("psp", "psp_gamma")], ren, "dunning_recovery_rate")
    out = score_cohorts([_analysis(locus=(("psp", "psp_beta"), ("payment_method_type", "card")))], [_cand()], [_rec()], W1)
    assert out["summary"]["top1_exact"] == 0.0 and out["summary"]["top1_equivalent_exact"] == 0.0


def test_injected_traffic_and_new_cohort_label_expectations():
    fraud = _rec(scenario_kind="fraud_like_spike")
    assert score_cohorts([_analysis(label="mix_shift")], [_cand()], [fraud], W1)["records"][0]["label_ok"] is True
    assert score_cohorts([_analysis(label="rate_change")], [_cand()], [fraud], W1)["records"][0]["label_ok"] is False
    app = _rec(scenario_kind="checkout_regression_app_version")
    assert score_cohorts([_analysis(label="new_cohort")], [_cand()], [app], W1)["records"][0]["label_ok"] is True
