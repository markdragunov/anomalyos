"""Scenario calendar (ADR-029): fixed mode keeps sim-1.0, randomized mode varies per seed within rules."""

from __future__ import annotations

import json

import pytest

from anomalyos.simulation import schedule as sch
from anomalyos.simulation.ground_truth import GENERATOR_VERSION
from anomalyos.simulation.runner import generate, run_id_for
from anomalyos.simulation.scenarios import FACTORIES, PRESETS, build_catalog
from anomalyos.simulation.seeds import DEMO_SEED, DEV_SEEDS, HELDOUT_SEEDS
from anomalyos.simulation.validate import EventValidator, validate_ground_truth
from anomalyos.simulation.world import DAY, HOUR, WorldConfig

# sim-1.0 start of each scenario relative to the world start (docs/SIMULATION.md section 5; day N = start + N days).
FIXED_STARTS = {
    "normal_variation": 1 * DAY,
    "psp_authorization_degradation": 2 * DAY + 14 * HOUR,
    "country_degradation": 4 * DAY + 9 * HOUR,
    "payment_method_degradation": 6 * DAY + 18 * HOUR,
    "checkout_regression_app_version": 9 * DAY + 10 * HOUR,
    "subscription_renewal_failure": 12 * DAY,
    "refund_spike": 15 * DAY + 8 * HOUR,
    "duplicate_charge": 16 * DAY + 11 * HOUR,
    "fraud_like_spike": 18 * DAY + 2 * HOUR,
    "gradual_degradation": 19 * DAY,
    "harmless_seasonality": 20 * DAY + 12 * HOUR,
    "small_cohort_noisy_anomaly": 21 * DAY + 3 * HOUR,
    "correlated_unrelated_anomalies": 22 * DAY + 10 * HOUR,
    "recovery_after_degradation": 24 * DAY + 20 * HOUR,
}


def spec_by_kind(world, preset="full"):
    return {s.kind: s for s in build_catalog(world, preset)}


# ------------------------------------------------------------------------------ fixed mode
@pytest.mark.parametrize("seed", [1, 5, 42, 1001])
def test_fixed_mode_keeps_the_sim_1_0_calendar(seed):
    w = WorldConfig(seed=seed)
    got = {k: s.start - w.start for k, s in spec_by_kind(w).items()}
    assert got == FIXED_STARTS  # identical on every seed: that is the sim-1.0 weakness randomized mode fixes


def test_fixed_run_id_ignores_the_new_field_so_sim_1_0_digests_stay_valid():
    from tests.unit.simulation.test_sim_golden import SEED5_SMALL
    fixed = run_id_for(WorldConfig(seed=5, scale=0.05), "full")
    assert fixed == SEED5_SMALL["run_id"]  # pinned value predates nothing but the version bump: the new field is not hashed
    assert run_id_for(WorldConfig(seed=5, scale=0.05, schedule="randomized"), "full") != fixed


def test_invalid_schedule_mode_is_rejected():
    with pytest.raises(ValueError, match="schedule"):
        WorldConfig(schedule="random")


# ------------------------------------------------------------------------------ randomized mode
def rand_world(seed, **kw):
    return WorldConfig(seed=seed, schedule="randomized", **kw)


def test_seed_sets_are_fixed_and_disjoint():
    assert len(DEV_SEEDS) == len(HELDOUT_SEEDS) == 20
    assert not set(DEV_SEEDS) & set(HELDOUT_SEEDS)
    assert DEMO_SEED not in DEV_SEEDS and DEMO_SEED not in HELDOUT_SEEDS
    assert len(set(DEV_SEEDS)) == len(DEV_SEEDS) and len(set(HELDOUT_SEEDS)) == len(HELDOUT_SEEDS)


def test_starts_differ_across_seeds():
    plans = [sch.build_schedule(rand_world(s)).placements for s in DEV_SEEDS[:10]]
    for kind in sch.PRIORITY:
        starts = {p[kind].start for p in plans}
        need = 5 if kind == "normal_variation" else 6  # the control day moves by whole days only (26 options)
        assert len(starts) >= need, f"{kind}: only {len(starts)} distinct starts on 10 seeds"


def test_cohorts_and_strengths_vary_too():
    plans = [sch.build_schedule(rand_world(s)).placements for s in DEV_SEEDS]
    assert len({p["psp_authorization_degradation"].params["psp"] for p in plans}) >= 2
    assert len({p["psp_authorization_degradation"].params["magnitude"] for p in plans}) >= 10
    assert len({(p["gradual_degradation"].params["country"], p["gradual_degradation"].params["psp"]) for p in plans}) >= 3
    assert len({p["checkout_regression_app_version"].params["platform"] for p in plans}) == 2


@pytest.mark.parametrize("seed", [*DEV_SEEDS[:5], *HELDOUT_SEEDS[:5]])
def test_windows_are_inside_the_world_and_respect_the_gap_rules(seed):
    w = rand_world(seed)
    placed = sch.build_schedule(w).placements
    assert set(placed) == set(FIXED_STARTS)
    for k, p in placed.items():
        assert p.start >= w.start + sch.WARMUP, k
        assert p.end <= (w.end if k in sch.OPEN_ENDED else w.end - sch.END_MARGIN), k
    kinds = sorted(placed)
    for i, a in enumerate(kinds):
        for b in kinds[i + 1:]:
            pa, pb = placed[a], placed[b]
            overlap = not (pa.end + sch.GAP <= pb.start or pb.end + sch.GAP <= pa.start)
            long_a, long_b = a in sch.LONG_RUNNING, b in sch.LONG_RUNNING
            if "normal_variation" in (a, b) or long_a == long_b:
                assert not overlap, f"{a} and {b} must keep a {sch.GAP // HOUR} h gap (seed {seed})"


def test_correlated_pair_overlaps_by_design_and_outage_lies_inside_the_campaign():
    for seed in DEV_SEEDS:
        p = sch.build_schedule(rand_world(seed)).placements["correlated_unrelated_anomalies"].params
        assert p["camp_start"] < p["out_start"] and p["out_end"] <= p["camp_end"] - HOUR


def test_placement_succeeds_on_many_seeds():
    # a pure function (no events are generated), so 1,000 seeds take well under a second
    for seed in range(1, 1001):
        sch.build_schedule(rand_world(seed))


def test_same_seed_gives_identical_plans_and_catalogs():
    w = rand_world(7)
    first = sch.build_schedule(w)
    sch.build_schedule.cache_clear()
    again = sch.build_schedule(rand_world(7))
    assert first == again
    assert [s.spec_hash for s in build_catalog(w)] == [s.spec_hash for s in build_catalog(rand_world(7))]


def test_plan_does_not_depend_on_scenario_dict_order():
    w = rand_world(11)
    in_order = {s.scenario_id: (s.start, s.end, [e.describe() for e in s.effects]) for s in
                (FACTORIES[k](w) for k in PRESETS["full"])}
    reversed_ = {s.scenario_id: (s.start, s.end, [e.describe() for e in s.effects]) for s in
                 (FACTORIES[k](w) for k in reversed(PRESETS["full"]))}
    assert in_order == reversed_


def test_truth_keys_and_effect_ids_are_unique_and_event_safe():
    for seed in DEV_SEEDS:
        specs = build_catalog(rand_world(seed))
        keys = [t.key for s in specs for t in s.truths]
        fx = [e.effect_id for s in specs for e in s.effects]
        assert len(keys) == len(set(keys)) and len(fx) == len(set(fx))


def test_truth_records_agree_with_the_effects_they_describe():
    for seed in (*DEV_SEEDS[:6], 42):
        for mode in ("fixed", "randomized"):
            w = WorldConfig(seed=seed, schedule=mode)
            for sp in build_catalog(w):
                for t in sp.truths:
                    fx = [e for e in sp.effects if e.effect_id in t.effect_ids]
                    if not fx:
                        continue
                    assert t.start == min(e.start for e in fx), (mode, sp.kind)  # truth start is the realized effect start
                    for e in fx:  # the affected cohort is where the effect actually acts
                        assert any(all(set(e.selector.get(d, ())) >= set(v) or set(v) <= set(e.selector.get(d, ())) for d, v in c.items() if d in e.selector)
                                   for c in t.affected_cohorts) or t.locus


def test_overlapping_records_of_different_scenarios_are_marked_unrelated_both_ways():
    found = 0
    for seed in range(1, 41):
        specs = build_catalog(rand_world(seed))
        by_key = {t.key: t for s in specs for t in s.truths}
        owner = {t.key: s.scenario_id for s in specs for t in s.truths}
        for key, t in by_key.items():
            for other in t.unrelated_to:
                assert key in by_key[other].unrelated_to, (seed, key, other)
                found += owner[key] != owner[other]
    assert found > 0  # long x short overlaps do occur


def test_release_train_follows_the_regression_scenario():
    for seed in DEV_SEEDS:
        w = rand_world(seed)
        plan = sch.build_schedule(w)
        reg = plan.placements["checkout_regression_app_version"].params
        rel = {(r.platform, r.version): r.released for r in plan.releases}
        assert rel[("android", "5.14.0")] == rel[("ios", "5.14.0")] == reg["start"]
        assert rel[(reg["platform"], "5.14.1")] == reg["fix"] and reg["fix"] - reg["start"] in {h * HOUR for h in range(36, 73)}
        other = "ios" if reg["platform"] == "android" else "android"
        assert (other, "5.14.1") not in rel
        for platform in ("ios", "android"):
            # scheduled releases keep 48 h between them; the hotfix follows its own 36-72 h lag (checked above)
            scheduled = sorted(r.released for r in plan.releases if r.platform == platform and r.version != "5.14.1")
            assert all(b - a >= 48 * HOUR for a, b in zip(scheduled, scheduled[1:])), (seed, platform)
            after = sorted(r.released for r in plan.releases if r.platform == platform)
            assert all(b - a >= 24 * HOUR for a, b in zip(after, after[1:])), (seed, platform)


def test_randomized_needs_a_28_day_world():
    with pytest.raises(sch.ScheduleError, match="28"):
        sch.build_schedule(WorldConfig(seed=1, days=14, schedule="randomized"))


@pytest.mark.parametrize("seed", [3, 1001])
def test_randomized_world_generates_valid_events_and_matching_ground_truth(seed, tmp_path):
    w = rand_world(seed, scale=0.05)
    res = generate(w, "full", tmp_path / "run")
    v = EventValidator()
    import gzip
    with gzip.open(tmp_path / "run" / "events.jsonl.gz", "rt") as f:
        for line in f:
            v.feed(json.loads(line), line)
    rep = v.finish()
    assert rep.ok, rep.errors[:5]
    records = [g.to_dict() for g in res.ground_truth]
    grep = validate_ground_truth(records, [s["scenario_id"] for s in res.manifest["scenarios"]])
    assert grep.ok, grep.errors[:5]
    specs = {s.scenario_id: s for s in build_catalog(w)}
    for g in records:
        sp = specs[g["scenario_id"]]
        t = next(t for t in sp.truths if t.key == g["record_key"])
        assert g["start"] == t.start and g["end"] == t.end  # ground truth carries the realized window
        for eid, params in g["expected_metric_effect"]["parameters"].items():
            fx = next(e for e in sp.effects if e.effect_id == eid)
            assert params["magnitude"] == fx.magnitude and params["profile"][0][0] == fx.start
    assert res.manifest["generator_version"] == GENERATOR_VERSION


def test_randomized_generation_is_reproducible(tmp_path):
    w = rand_world(9, scale=0.03)
    a, b = generate(w, "full"), generate(w, "full")
    assert (a.events_digest, a.truth_digest, a.run_id) == (b.events_digest, b.truth_digest, b.run_id)
    c = generate(rand_world(10, scale=0.03), "full")
    assert c.events_digest != a.events_digest and c.run_id != a.run_id
