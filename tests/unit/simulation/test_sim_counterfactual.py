"""Ground-truth impact equals the real difference between a world without and a world with one scenario.

Independent check of the counterfactual (ADR-021): the same seed and scale are generated twice,
once with no scenario and once with exactly one, and the difference in event counts is compared
with `expected_impact` from the ground truth. The world is deterministic and a scenario perturbs
nothing outside its cohort and window, so the match is exact (tolerance 0) except where noted.
"""

from __future__ import annotations

import json

import pytest

from anomalyos.simulation.engine import Simulation
from anomalyos.simulation.scenarios import FACTORIES
from anomalyos.simulation.world import WorldConfig

WORLD = WorldConfig(seed=42, scale=0.1)


def _count(specs):
    sim = Simulation(WORLD, specs)
    c = {"pi_succeeded": 0, "refunds": 0, "keyless_checkout_pi": 0}
    for ev in sim.run():
        o = json.loads(ev.to_json_line())
        t = o["type"]
        if t == "payment_intent.succeeded":
            c["pi_succeeded"] += 1
        elif t == "refund.created":
            c["refunds"] += 1
        elif t == "payment_intent.created" and o["data"]["object"]["metadata"]["channel"] == "checkout" \
                and o["request"]["idempotency_key"] is None:
            c["keyless_checkout_pi"] += 1
    return c, sim


@pytest.fixture(scope="module")
def baseline():
    counts, _ = _count(())
    return counts


def _impact(kind):
    counts, sim = _count((FACTORIES[kind](WORLD),))
    impacts = [g.expected_impact for g in sim.ground_truth()]
    return counts, {k: sum(i[k] for i in impacts) for k in ("lost_successful_payments", "extra_refunds", "duplicate_charges")}


def test_psp_degradation_lost_payments_match_world_difference(baseline):
    counts, impact = _impact("psp_authorization_degradation")
    assert impact["lost_successful_payments"] > 0
    assert baseline["pi_succeeded"] - counts["pi_succeeded"] == impact["lost_successful_payments"]


def test_renewal_failure_lost_payments_match_world_difference(baseline):
    counts, impact = _impact("subscription_renewal_failure")
    assert impact["lost_successful_payments"] > 0
    assert baseline["pi_succeeded"] - counts["pi_succeeded"] == impact["lost_successful_payments"]


def test_refund_spike_extra_refunds_match_world_difference(baseline):
    counts, impact = _impact("refund_spike")
    assert impact["extra_refunds"] > 0
    assert counts["refunds"] - baseline["refunds"] == impact["extra_refunds"]


def test_duplicate_charges_match_keyless_payment_intents(baseline):
    counts, impact = _impact("duplicate_charge")
    assert impact["duplicate_charges"] > 0
    assert counts["keyless_checkout_pi"] - baseline["keyless_checkout_pi"] == impact["duplicate_charges"]


def test_checkout_regression_lost_payments_within_one_of_world_difference(baseline):
    """Tolerance 1 (not 0): when a lost payment disappears, a rare organic duplicate that would have followed it
    disappears too, so the real difference can exceed the counted loss by one payment. Seen at scale 0.3,
    seed 42: 578 against 577 (6,499 usd). At this scale the match is exact, but the contract is +/- 1."""
    counts, impact = _impact("checkout_regression_app_version")
    assert impact["lost_successful_payments"] > 0
    assert abs((baseline["pi_succeeded"] - counts["pi_succeeded"]) - impact["lost_successful_payments"]) <= 1
