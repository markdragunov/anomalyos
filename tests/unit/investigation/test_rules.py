"""Outcome rule, hypotheses and the check library (ADR-049 D-2, D-3, D-4) on hand-built inputs."""

from __future__ import annotations

from pulseos.investigation import checks
from pulseos.investigation.hypotheses import initial, prior, ranking
from pulseos.investigation.measure import outcome


def test_outcome_thresholds():
    assert [outcome(z, "down") for z in (-2.5, -1.5, -0.5, 1.5, 2.5, None)] == \
        ["moved", "ambiguous", "unchanged", "ambiguous", "opposite", "insufficient"]
    assert outcome(3.0, "up") == "moved"


def test_prior_and_initial_hypotheses():
    p = prior("authorization_rate", "down", {"psp", "customer_country"}, "rate_change")
    assert p["psp_degradation"] == 3 and p["issuer_or_country_degradation"] == 3 and p["fraud_attack"] == 1
    hs = initial("inv_x", p, 5)
    causes = [h.cause for h in hs]
    assert causes[-2:] == ["normal_variation", "unknown"] and len(causes) <= 7
    assert prior("attempt_volume", "up", set(), "volume_only")["normal_variation"] == 3
    assert prior("late_arrival_share", "up", set(), "rate_change") == {"data_pipeline_issue": 3}


def test_status_rules_and_ranking():
    hs = initial("inv_x", {"psp_degradation": 3, "fraud_attack": 1}, 5)
    h = hs[0]
    h.supporting += ["e1", "e2"]
    h.refresh()
    assert h.status == "supported"
    h.contradicting += ["e3"]
    h.refresh()
    assert h.status == "open"
    h.contradicting += ["e4", "e5"]
    h.refresh()
    assert h.status == "contradicted" and h.contradicting == ["e3", "e4", "e5"]  # kept, never dropped
    assert ranking(hs)[0].cause != "psp_degradation"


def test_sibling_predictions_follow_the_cause_dimension():
    causes = ["psp_degradation", "issuer_or_country_degradation", "normal_variation", "unknown"]
    lib = checks.build("authorization_rate", "down", {"psp": "b", "customer_country": "DE"},
                       {"psp": ["a"], "customer_country": ["FR"]}, causes, None, ["b"])
    by = {(c.kind, c.args[2] if c.kind == "sibling" else c.args[0]): c for c in lib}
    other_psp = by[("sibling", (("customer_country", "DE"), ("psp", "a")))]
    other_country = by[("sibling", (("customer_country", "FR"), ("psp", "b")))]
    assert other_psp.predicted("psp_degradation") == "unchanged" and other_psp.predicted("issuer_or_country_degradation") == "moved"
    assert other_country.predicted("psp_degradation") == "moved" and other_country.predicted("issuer_or_country_degradation") == "unchanged"
    assert any(c.kind == "status" and c.predicted("psp_degradation") == "present:authorization" for c in lib)


def test_update_supports_contradicts_and_side_checks_only_support():
    hs = initial("inv_x", {"psp_degradation": 3, "issuer_or_country_degradation": 3}, 5)
    sib = checks.Check("c1", "sibling", "compare_cohorts", ("m",), (("psp_degradation", "unchanged"),
                                                                   ("issuer_or_country_degradation", "moved")))
    checks.update(hs, sib, "unchanged", "evd_1")
    d = {h.cause: h for h in hs}
    assert d["psp_degradation"].supporting == ["evd_1"] and d["issuer_or_country_degradation"].contradicting == ["evd_1"]
    st = checks.Check("c2", "status", "check_psp_status", ("b", "start_6h"), (("psp_degradation", "present:authorization"),))
    checks.update(hs, st, frozenset(), "evd_2")  # absence never contradicts
    assert d["psp_degradation"].contradicting == []
    checks.update(hs, st, frozenset({"present:authorization"}), "evd_3")
    assert "evd_3" in d["psp_degradation"].supporting


def test_shortlist_keeps_a_disconfirming_check_and_skips_done_ones():
    hs = initial("inv_x", {"psp_degradation": 3}, 5)
    side = [checks.Check(f"s{i}", "deploy", "get_deployments", (f"svc{i}", "onset_2h"), (("psp_degradation", "present:x"),))
            for i in range(6)]
    metric = checks.Check("m1", "related", "get_related_metrics", ("x",), (("psp_degradation", "moved"),))
    sl = checks.shortlist(side + [metric], set(), hs, 3)
    assert len(sl) == 3 and any(checks.disconfirming(c, ranking(hs)[0]) for c in sl)
    assert metric not in checks.shortlist(side + [metric], {metric.key}, hs, 3)


def test_channel_split_check_separates_renewal_from_payment_causes():
    p = prior("authorization_rate", "down", {"psp"}, "rate_change")
    assert p["renewal_job_failure"] == 1 and p["dunning_failure"] == 1
    causes = [h.cause for h in initial("inv_x", p, 7)]
    lib = checks.build("authorization_rate", "down", {"psp": "b"}, {}, causes, None, [])
    ch = next(c for c in lib if c.kind == "channel")
    assert dict(ch.args[1]) == {"psp": "b", "channel": "checkout"} and ch.tool == "get_metric_history"
    assert ch.predicted("renewal_job_failure") == "unchanged" and ch.predicted("psp_degradation") == "moved"
    assert checks.disconfirming(ch, next(h for h in initial("inv_x", p, 7) if h.cause == "psp_degradation"))
    assert not any(c.kind == "channel" for c in checks.build("checkout_conversion_rate", "down", {"platform": "web"}, {},
                                                               causes, None, []))
