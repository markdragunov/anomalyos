"""Scenario engine contracts and 'ground truth is visible in the data' checks."""

from __future__ import annotations

from collections import defaultdict

import pytest

from anomalyos.simulation.ground_truth import Mechanism, RootCause, Route, Severity, TruthSpec
from anomalyos.simulation.scenarios import FACTORIES, PRESETS, Effect, ScenarioSpec, build_catalog, degrade_then_recover, ramp, step
from anomalyos.simulation.world import DAY, HOUR, WorldConfig

REQUIRED_KINDS = {
    "psp_authorization_degradation", "country_degradation", "payment_method_degradation",
    "checkout_regression_app_version", "subscription_renewal_failure", "refund_spike", "duplicate_charge",
    "fraud_like_spike", "gradual_degradation", "harmless_seasonality", "small_cohort_noisy_anomaly",
    "correlated_unrelated_anomalies", "recovery_after_degradation",
}


def test_catalog_covers_required_scenarios():
    w = WorldConfig()
    kinds = {s.kind for s in build_catalog(w, "full")}
    assert REQUIRED_KINDS <= kinds
    assert set(PRESETS["core"]) == {"normal_variation", "psp_authorization_degradation", "gradual_degradation",
                                    "recovery_after_degradation"}
    for s in build_catalog(w, "full"):
        assert s.truths and s.seed and s.spec_hash
        assert w.start <= s.start < w.end


def test_catalog_is_pure():
    a = [s.spec_hash for s in build_catalog(WorldConfig(seed=3), "full")]
    assert a == [s.spec_hash for s in build_catalog(WorldConfig(seed=3), "full")]
    assert a != [s.spec_hash for s in build_catalog(WorldConfig(seed=4), "full")]


def test_catalog_rejects_short_world_and_unknown_preset():
    with pytest.raises(ValueError):
        build_catalog(WorldConfig(days=7), "full")
    with pytest.raises(ValueError):
        build_catalog(WorldConfig(), "nope")


def test_intensity_profiles():
    t0 = 1_000_000
    assert Effect("a", Mechanism.APPROVAL, {}, step(t0, t0 + 10), .5).intensity(t0 + 9) == 1.0
    assert Effect("a", Mechanism.APPROVAL, {}, step(t0, t0 + 10), .5).intensity(t0 + 10) == 0.0
    r = Effect("a", Mechanism.APPROVAL, {}, ramp(t0, t0 + 100, t0 + 200), .5)
    assert r.intensity(t0) == 0.0 and r.intensity(t0 + 50) == pytest.approx(.5) and r.intensity(t0 + 150) == 1.0
    d = Effect("a", Mechanism.APPROVAL, {}, degrade_then_recover(t0, t0 + 100, t0 + 200), .5)
    assert d.intensity(t0 + 99) == 1.0 and d.intensity(t0 + 150) == pytest.approx(.5) and d.intensity(t0 + 200) == 0.0


def test_spec_invariants():
    t = TruthSpec("k", ("fx",), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE, 0, 1, {}, "m", (), (), "x", "none", None, None)
    with pytest.raises(ValueError):  # orphan effect
        ScenarioSpec("s", "k", "t", 1, 0, 1, Severity.NONE, (), (t,))
    with pytest.raises(ValueError):  # affected == control
        TruthSpec("k", (), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE, 0, 1, {}, "m",
                  ({"customer_country": ("US",)},), ({"customer_country": ("US",)},), "x", "none", None, None)
    with pytest.raises(ValueError):  # incident without detection window
        TruthSpec("k", (), RootCause.FRAUD_ATTACK, Route.INCIDENT, Severity.HIGH, 0, 1, {}, "m", (), (), "x", "up", None, None)


def test_truth_record_fields(full_run):
    recs = {g.record_key: g for g in full_run["res"].ground_truth}
    psp = recs["psp_beta_auth"]
    assert psp.incident_id and psp.incident_id.startswith("inc_")
    assert psp.expected_route == "incident" and psp.true_cause == psp.root_cause["cause"]
    assert psp.expected_detection_window["start"] == psp.start and psp.expected_recovery == psp.end
    assert recs["es_gamma_drift"].end is None and recs["es_gamma_drift"].expected_recovery is None
    for k in ("br_holiday_demand", "jp_amex_blip", "de_campaign", "control_day"):
        assert recs[k].incident_id is None and recs[k].expected_route == "suppress"
    assert recs["ideal_outage"].unrelated_to == ["scn_correlated_unrelated:de_campaign"]
    assert recs["de_campaign"].unrelated_to == [recs["ideal_outage"].incident_id]
    rec = recs["android_5140_checkout"]
    assert rec.end < rec.expected_recovery  # impact outlives the hotfix while users update


# --- ground truth must be observable in the emitted events -----------------------------------------

def _charges(events, t0, t1):
    for e in events:
        if e["type"] in ("charge.succeeded", "charge.failed") and t0 <= e["created"] < t1:
            o = e["data"]["object"]
            yield o, e["type"] == "charge.succeeded"


def test_psp_outage_visible_against_controls(full_run):
    g = next(x for x in full_run["res"].ground_truth if x.record_key == "psp_beta_auth")
    agg = defaultdict(lambda: [0, 0])
    for o, ok in _charges(full_run["events"], g.start, g.end):
        agg[o["metadata"]["psp"]][0] += ok
        agg[o["metadata"]["psp"]][1] += 1
    rate = {k: s / n for k, (s, n) in agg.items()}
    assert rate["psp_beta"] < rate["psp_alpha"] - 0.15 and rate["psp_beta"] < rate["psp_gamma"] - 0.15


def test_checkout_regression_hits_conversion_not_approval(full_run):
    g = next(x for x in full_run["res"].ground_truth if x.record_key == "android_5140_checkout")
    created, ok = defaultdict(int), defaultdict(int)
    appr = defaultdict(lambda: [0, 0])
    for e in full_run["events"]:
        o = e["data"]["object"]
        if not (g.start <= o.get("created", 0) < g.end) or o.get("object") not in ("payment_intent", "charge"):
            continue
        key = (o["metadata"]["platform"], o["metadata"]["app_version"])
        if e["type"] == "payment_intent.created":
            created[key] += 1
        elif e["type"] == "payment_intent.succeeded":
            ok[key] += 1
        elif e["type"] in ("charge.succeeded", "charge.failed"):
            appr[key][0] += e["type"] == "charge.succeeded"
            appr[key][1] += 1
    bad, good = ("android", "5.14.0"), ("ios", "5.14.0")
    assert ok[bad] / created[bad] < ok[good] / created[good] - 0.2
    assert abs(appr[bad][0] / appr[bad][1] - appr[good][0] / appr[good][1]) < 0.08


def test_duplicates_are_non_idempotent_and_match_truth(full_run):
    g = next(x for x in full_run["res"].ground_truth if x.record_key == "web_duplicate_charges")
    dup = sum(1 for e in full_run["events"] if e["type"] == "payment_intent.created"
              and e["request"]["idempotency_key"] is None and e["data"]["object"]["metadata"]["channel"] == "checkout"
              and g.start <= e["created"] < g.end + 60)
    assert g.expected_impact["duplicate_charges"] > 0
    assert dup >= g.expected_impact["duplicate_charges"]  # effect-caused ⊆ all non-idempotent duplicates


def test_cause_vocabulary_matches_data_model():
    """Ground truth and Jev must use identical labels (DATA_MODEL.md, cause vocabulary v1)."""
    import re
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[3] / "docs" / "DATA_MODEL.md").read_text()
    section = doc.split("## Cause vocabulary", 1)[1].split("Initial set (v1):", 1)[1].strip().split("\n\n", 1)[0]
    documented = set(re.findall(r"`([a-z_]+)`", section))
    assert {c.value for c in RootCause} == documented


def test_truth_carries_data_model_fields(full_run):
    recs = {g.record_key: g.to_dict() for g in full_run["res"].ground_truth}
    for r in recs.values():
        for f in ("scenario_id", "scenario_type", "seed", "is_incident", "onset_at", "end_at", "ramp",
                  "affected_cohorts", "affected_metrics", "injected_effect", "true_cause", "true_impact"):
            assert f in r, f
        assert r["is_incident"] == (r["incident_id"] is not None)
        assert r["onset_at"] == r["start"] and r["scenario_type"] == r["scenario_kind"]
    assert recs["psp_beta_auth"]["ramp"] == "step"
    assert recs["es_gamma_drift"]["ramp"] == "gradual"
    assert recs["gb_alpha_recovery"]["ramp"] == "recovering"
    assert recs["control_day"]["ramp"] == "none" and recs["control_day"]["true_cause"] == "normal_variation"
    ti = recs["psp_beta_auth"]["true_impact"]
    assert ti["affected_payment_count"] > 0 and sum(ti["lost_revenue_minor"].values()) > 0
