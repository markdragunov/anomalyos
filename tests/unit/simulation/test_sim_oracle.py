"""sim-1.1.1 phase 3: `oracle_detectable_at` reflects when an effect becomes statistically distinguishable."""

from __future__ import annotations

import pytest

from pulseos.simulation.ground_truth import oracle_detectability
from pulseos.simulation.runner import generate
from pulseos.simulation.validate import validate_ground_truth
from pulseos.simulation.world import DAY, HOUR, WorldConfig


@pytest.fixture(scope="module")
def seed42():
    res = generate(WorldConfig(seed=42, scale=0.3), "full")
    return res, {g.record_key: g for g in res.ground_truth}


def test_psp_outage_is_distinguishable_within_the_first_hour(seed42):
    _, g = seed42
    t = g["psp_beta_auth"]
    assert t.oracle_detectable_at is not None and t.oracle_detectable_at - t.start <= HOUR


def test_gradual_drift_is_distinguishable_between_day_2_and_day_5_of_the_ramp(seed42):
    _, g = seed42
    t = g["es_gamma_drift"]
    assert t.oracle_detectable_at is not None
    assert 2 * DAY <= t.oracle_detectable_at - t.start <= 5 * DAY


def test_suppressed_signals_have_no_oracle_time(seed42):
    _, g = seed42
    for key in ("jp_amex_blip", "control_day", "br_holiday_demand", "de_campaign"):
        assert g[key].oracle_detectable_at is None
    assert all(r.oracle_detectable_at is None for r in g.values() if r.expected_route == "suppress")


def test_oracle_fields_are_consistent_and_validated(seed42):
    res, g = seed42
    for r in g.values():
        if r.expected_route == "incident":
            assert r.oracle_method and "z=3" in r.oracle_method
            assert r.expected_detection_window["basis"] == "designer_constant"
            if r.oracle_detectable_at is not None:
                assert r.oracle_detectable_at >= r.start and (r.end is None or r.oracle_detectable_at <= r.end)
    rep = validate_ground_truth([x.to_dict() for x in res.ground_truth], [s["scenario_id"] for s in res.manifest["scenarios"]])
    assert rep.ok, rep.errors[:5]


def test_validator_rejects_an_oracle_time_outside_the_window(seed42):
    res, _ = seed42
    records = [x.to_dict() for x in res.ground_truth]
    bad = next(r for r in records if r["record_key"] == "psp_beta_auth")
    bad["oracle_detectable_at"] = bad["start"] - 1
    rep = validate_ground_truth(records, [s["scenario_id"] for s in res.manifest["scenarios"]])
    assert any("oracle_detectable_at" in e for e in rep.errors)


def test_oracle_function_on_hand_built_counts():
    start = 100 * HOUR
    # 1,000 attempts/hour; counterfactual approval 0.9; actual 0.8 from the second hour on.
    hourly = {100: {"attempts_actual": 1000, "success_actual": 900, "attempts_cf": 1000, "success_cf": 900},
              101: {"attempts_actual": 1000, "success_actual": 800, "attempts_cf": 1000, "success_cf": 900}}
    at, method = oracle_detectability("charge_approval_rate", hourly, start, {})
    assert at == 102 * HOUR and "binomial" in method
    no_effect = {h: {"attempts_actual": 50, "success_actual": 45, "attempts_cf": 50, "success_cf": 45} for h in range(100, 110)}
    assert oracle_detectability("charge_approval_rate", no_effect, start, {})[0] is None
    counts = {100: {"extra_refunds": 3, "pi_succeeded_cf": 100}, 101: {"extra_refunds": 10, "pi_succeeded_cf": 100}}
    at, method = oracle_detectability("refund_rate", counts, start, {"organic_refund_rate": 0.03})
    assert at == 102 * HOUR and "Poisson" in method  # hour 100: 3/sqrt(3) < 3; hour 101: 13/sqrt(6) > 3 -> end of hour 101


@pytest.mark.slow
def test_every_incident_of_the_fixed_calendar_is_distinguishable_at_full_scale():
    res = generate(WorldConfig(seed=42, scale=1.0), "full")
    missing = [g.record_key for g in res.ground_truth if g.expected_route == "incident" and g.oracle_detectable_at is None]
    assert missing == []
