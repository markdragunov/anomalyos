"""sim-1.1 phase 2: decline codes and risk scores must not reveal whether a decline was caused by an incident."""

from __future__ import annotations

import collections
import json

import pytest

from anomalyos.simulation.engine import Simulation
from anomalyos.simulation.scenarios import build_catalog
from anomalyos.simulation.world import EFFECT_ONLY_DECLINE_CODES, ORGANIC_CARD_DECLINES, ORGANIC_LOCAL_DECLINES, WorldConfig

ORGANIC = {(a, b) for a, b, _ in ORGANIC_CARD_DECLINES + ORGANIC_LOCAL_DECLINES}


@pytest.mark.parametrize("mode", ["fixed", "randomized"])
def test_every_effect_decline_code_also_occurs_organically(mode):
    for seed in range(1, 21):
        for sp in build_catalog(WorldConfig(seed=seed, schedule=mode)):
            for e in sp.effects:
                for a, b, _ in e.decline_codes:
                    assert (a, b) in ORGANIC or (a, b) in EFFECT_ONLY_DECLINE_CODES, (e.effect_id, a, b)


def test_effect_only_codes_belong_to_the_attack_only():
    for seed in range(1, 21):
        for sp in build_catalog(WorldConfig(seed=seed, schedule="randomized")):
            for e in sp.effects:
                if any((a, b) in EFFECT_ONLY_DECLINE_CODES for a, b, _ in e.decline_codes):
                    assert sp.kind == "fraud_like_spike", e.effect_id


@pytest.fixture(scope="module")
def failed_charges():
    """Failed checkout charges of seed 42 / scale 0.1, labelled by whether an effect caused them (engine-internal truth)."""
    w = WorldConfig(seed=42, scale=0.1)
    sim = Simulation(w, build_catalog(w))
    out = []
    windows = [(e.start, e.end) for sp in sim.specs for e in sp.effects if sp.kind != "fraud_like_spike"]
    fraud = [(e.start, e.end) for sp in sim.specs for e in sp.effects if sp.kind == "fraud_like_spike"]
    for ev in sim.run():
        o = json.loads(ev.to_json_line())
        if o["type"] != "charge.failed":
            continue
        obj = o["data"]["object"]
        t = o["created"]
        if any(a <= t < b for a, b in fraud):
            continue
        in_window = any(a <= t < b for a, b in windows)
        out.append((obj["outcome"]["reason"], obj["outcome"]["risk_score"], in_window))
    return out


def test_risk_score_does_not_separate_incident_declines_from_organic(failed_charges):
    inside = [r for _, r, w in failed_charges if w]
    outside = [r for _, r, w in failed_charges if not w]
    assert len(inside) > 100 and len(outside) > 500  # sim-1.2 windows cover more of the month
    assert abs(sum(inside) / len(inside) - sum(outside) / len(outside)) <= 1.0


def test_issuer_not_available_occurs_outside_incident_windows(failed_charges):
    outside = collections.Counter(code for code, _, w in failed_charges if not w)
    share = outside["issuer_not_available"] / sum(outside.values())
    assert 0 < share < 0.05
