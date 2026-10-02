"""sim-1.2 phase 5: new scenarios cover the whole cause vocabulary and behave as designed."""

from __future__ import annotations

import collections
import json

import pytest

from anomalyos.events.normalize import normalize
from anomalyos.simulation.engine import Simulation
from anomalyos.simulation.ground_truth import RootCause
from anomalyos.simulation.scenarios import FACTORIES, build_catalog
from anomalyos.simulation.world import DAY, HOUR, WorldConfig

WORLD = WorldConfig(seed=42, scale=0.3)


@pytest.fixture(scope="module")
def run():
    sim = Simulation(WORLD, build_catalog(WORLD))
    events = [json.loads(ev.to_json_line()) for ev in sim.run()]
    truth = {g.record_key: g for g in sim.ground_truth()}
    return sim, events, truth


def test_every_cause_of_vocabulary_v1_is_exercised():
    causes = {t.true_cause for sp in build_catalog(WorldConfig(seed=1)) for t in sp.truths}
    assert causes == set(RootCause)


@pytest.mark.parametrize("schedule", ["fixed", "randomized"])
def test_new_kinds_have_truth_controls_and_routes(schedule):
    for seed in (1, 2, 3):
        specs = {s.kind: s for s in build_catalog(WorldConfig(seed=seed, schedule=schedule))}
        for kind in ("dunning_failure", "pricing_or_plan_change", "data_pipeline_issue", "simultaneous_incidents",
                     "mix_shift_masking", "ambiguous_signal"):
            for t in specs[kind].truths:
                assert t.control_cohorts and t.affected_cohorts
        amb = specs["ambiguous_signal"].truths[0]
        assert amb.true_cause is RootCause.UNKNOWN and amb.expected_route.value == "watch"
        a, b = specs["simultaneous_incidents"].truths
        assert a.expected_route.value == b.expected_route.value == "incident" and b.key in a.unrelated_to and a.key in b.unrelated_to


def test_dunning_failure_hits_retries_only(run):
    _, _, truth = run
    m = truth["dunning_alpha"].expected_metric_effect["measured"]
    assert m["renewal_first_attempt_success_actual"] == m["renewal_first_attempt_success_counterfactual"]  # first attempts untouched
    assert truth["dunning_alpha"].expected_impact["lost_renewals"] > 0
    assert truth["dunning_alpha"].oracle_detectable_at is not None


def test_price_change_cancels_subscriptions_without_charging(run):
    sim, events, truth = run
    p = sim.specs[[s.kind for s in sim.specs].index("pricing_or_plan_change")]
    s, e = p.start, p.end
    canceled = [x for x in events if x["type"] == "customer.subscription.deleted" and s <= x["created"] < e
                and x["data"]["object"]["metadata"].get("customer_country") == "DE"]
    extra = truth["de_price_change"].expected_metric_effect["measured"].get("extra_cancellations") \
        or sim.tally.counts["fx_de_price_change"]["extra_cancellations"]
    assert extra > 0 and len(canceled) >= extra


def test_price_change_counterfactual_matches_world_difference():
    w = WorldConfig(seed=42, scale=0.3)
    def deleted(specs):
        sim = Simulation(w, specs)
        return sum(1 for ev in sim.run() if ev.type == "customer.subscription.deleted"), sim
    base, _ = deleted(())
    with_it, sim = deleted((FACTORIES["pricing_or_plan_change"](w),))
    assert with_it - base == sim.tally.counts["fx_de_price_change"]["extra_cancellations"] > 0


def test_pipeline_delay_moves_only_delivery_and_only_in_the_cohort(run):
    sim, events, truth = run
    late = [x for x in events if "delivered_at" in x]
    assert late
    for x in late:
        assert HOUR <= x["delivered_at"] - x["created"] <= 2 * HOUR
        assert x["data"]["object"].get("metadata", {}).get("psp") == "psp_gamma"
    norm = list(normalize(list(enumerate(events)), "run_0123456789abcdef"))
    lagged = [r for r in norm if r["ingested_at"] > r["occurred_at"]]
    assert lagged and all(r["psp"] == "psp_gamma" for r in lagged)
    assert truth["gamma_webhook_lag"].oracle_detectable_at is not None


def test_mix_shift_masks_the_drop_in_the_global_view(run):
    sim, events, _ = run
    sp = sim.specs[[s.kind for s in sim.specs].index("mix_shift_masking")]
    s, e = sp.start, sp.end
    def approval(pred, lo, hi):
        n = k = 0
        for x in events:
            if x["type"] in ("charge.succeeded", "charge.failed") and lo <= x["created"] < hi and pred(x["data"]["object"]["metadata"]):
                n += 1; k += x["type"] == "charge.succeeded"
        return k / n
    cohort = lambda m: (m.get("customer_country"), m.get("psp")) == ("BR", "psp_gamma")
    every = lambda m: True
    drop_cohort = approval(cohort, s - DAY, e - DAY) - approval(cohort, s, e)
    drop_global = approval(every, s - DAY, e - DAY) - approval(every, s, e)
    assert drop_cohort > 0.1 and abs(drop_global) < drop_cohort / 3


def test_no_late_events_in_worlds_without_the_pipeline_scenario():
    w = WorldConfig(seed=7, scale=0.05)
    specs = tuple(s for s in build_catalog(w) if s.kind != "data_pipeline_issue")
    assert not any(ev.delivered_at for ev in Simulation(w, specs).run())


# ------------------------------------------------------------------ phase 6: metric expectations, control day
def test_every_record_has_explicit_disjoint_metric_expectations(run):
    _, _, truth = run
    for key, g in truth.items():
        assert not set(g.affected_metrics) & set(g.unchanged_metrics), key
        if g.expected_route != "suppress":
            assert g.affected_metrics, key
    reg = truth["android_5140_checkout"]
    assert reg.affected_metrics == ["payment_intent_conversion_rate"]
    assert reg.unchanged_metrics == ["charge_approval_rate", "refund_rate"]
    assert truth["control_day"].affected_metrics == []


def test_unchanged_metrics_really_do_not_move_for_the_checkout_regression(run):
    _, _, truth = run
    m = truth["android_5140_checkout"].expected_metric_effect["measured"]
    assert abs(m["charge_approval_rate_actual"] - m["charge_approval_rate_counterfactual"]) < 0.02
    assert m["pi_conversion_counterfactual"] - m["pi_conversion_actual"] > 0.2


def test_control_day_has_reference_measurements(run):
    _, _, truth = run
    m = truth["control_day"].expected_metric_effect["measured"]
    assert m["charge_attempts"] > 1000 and 0.8 < m["charge_approval_rate_actual"] < 0.95
