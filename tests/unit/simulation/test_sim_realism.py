"""sim-1.2 phase 4: realism v2 (overdispersion, weekend dip, benign shocks); v1 stays byte-identical."""

from __future__ import annotations

import collections
import json

import pytest

from anomalyos.simulation.engine import Simulation
from anomalyos.simulation.runner import generate, run_id_for
from anomalyos.simulation.scenarios import build_catalog
from anomalyos.simulation.validate import validate_ground_truth
from anomalyos.simulation.world import WorldConfig


def dispersion_index(w: WorldConfig) -> dict[str, float]:
    """Hourly charge approval per PSP, scenario-free world: sum((k - n p)^2 / (n p (1 - p))) / (H - 1); 1.0 = binomial."""
    sim = Simulation(w, build_catalog(w, "baseline"))
    k, n = collections.Counter(), collections.Counter()
    for ev in sim.run():
        if ev.type not in ("charge.succeeded", "charge.failed"):
            continue
        o = json.loads(ev.to_json_line())
        key = (o["data"]["object"]["metadata"]["psp"], (o["created"] - w.start) // 3600)
        n[key] += 1
        k[key] += ev.type == "charge.succeeded"
    out = {}
    for psp in ("psp_alpha", "psp_beta", "psp_gamma"):
        hs = [h for (p, h) in n if p == psp and n[(p, h)] >= 30]
        p_bar = sum(k[(psp, h)] for h in hs) / sum(n[(psp, h)] for h in hs)
        out[psp] = sum((k[(psp, h)] - n[(psp, h)] * p_bar) ** 2 / (n[(psp, h)] * p_bar * (1 - p_bar)) for h in hs) / (len(hs) - 1)
    return out


def test_v1_is_the_default_and_its_run_id_is_unchanged():
    assert WorldConfig().realism == "v1"
    assert run_id_for(WorldConfig(seed=5, scale=0.05), "full") == run_id_for(WorldConfig(seed=5, scale=0.05, realism="v1"), "full")
    assert run_id_for(WorldConfig(seed=5, scale=0.05, realism="v2"), "full") != run_id_for(WorldConfig(seed=5, scale=0.05), "full")


def test_invalid_realism_is_rejected():
    with pytest.raises(ValueError, match="realism"):
        WorldConfig(realism="v3")


@pytest.mark.parametrize("schedule", ["fixed", "randomized"])
def test_benign_shocks_are_suppressed_non_incidents(schedule):
    for seed in range(1, 21):
        w = WorldConfig(seed=seed, realism="v2", schedule=schedule)
        specs = build_catalog(w)
        shocks = [s for s in specs if s.kind == "benign_shock"]
        assert len(shocks) == 1
        truths = shocks[0].truths
        assert 3 <= len(truths) <= 6
        control = next(s for s in specs if s.kind == "normal_variation")
        for t in truths:
            assert t.expected_route.value == "suppress" and t.true_cause.value == "normal_variation" and t.detection_delay_s is None
            assert t.end + 6 * 3600 <= control.start or control.end + 6 * 3600 <= t.start  # the control day stays clean
        for fx in shocks[0].effects:
            assert 0.90 <= fx.magnitude <= 0.95 and fx.end - fx.start <= 3 * 3600


def test_no_benign_shocks_in_v1_or_in_the_baseline_preset():
    assert not [s for s in build_catalog(WorldConfig(seed=1)) if s.kind == "benign_shock"]
    assert build_catalog(WorldConfig(seed=1, realism="v2"), "baseline") == ()


def test_v2_world_validates(tmp_path):
    res = generate(WorldConfig(seed=4, scale=0.05, realism="v2", schedule="randomized"), "full")
    rep = validate_ground_truth([g.to_dict() for g in res.ground_truth], [s["scenario_id"] for s in res.manifest["scenarios"]])
    assert rep.ok, rep.errors[:5]
    assert any(g.scenario_kind == "benign_shock" for g in res.ground_truth)


@pytest.mark.slow
def test_dispersion_index_v1_regression_and_v2_target():
    for seed in (1, 2, 3):
        v1 = dispersion_index(WorldConfig(seed=seed, realism="v1"))
        v2 = dispersion_index(WorldConfig(seed=seed, realism="v2"))
        assert all(1.0 <= x <= 1.6 for x in v1.values()), (seed, v1)  # sim-1.1 measured 1.23-1.43 with this formula
        assert all(1.45 <= x <= 2.1 for x in v2.values()), (seed, v2)  # target about 1.5-2.0
