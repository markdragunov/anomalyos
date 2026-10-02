"""Acceptance: deterministic seed reproduces an identical dataset; replay → identical truth."""

from __future__ import annotations

import hashlib

from anomalyos.simulation.ids import derive_seed, stable_id
from anomalyos.simulation.runner import generate, run_id_for
from anomalyos.simulation.scenarios import build_catalog
from anomalyos.simulation.world import DAY, HOUR, WorldConfig

TINY = dict(scale=0.02)


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ids_are_pure_and_stripe_shaped():
    a = stable_id("pi", 1, "co", 3, "US", 7)
    assert a == stable_id("pi", 1, "co", 3, "US", 7)
    assert a != stable_id("pi", 2, "co", 3, "US", 7)
    assert a.startswith("pi_") and len(a) == 3 + 24 and a[3:].isalnum()
    assert derive_seed(1, "hour", 5) != derive_seed(1, "hour", 6)


def test_same_seed_reproduces_identical_dataset(tmp_path):
    a = generate(WorldConfig(seed=99, **TINY), "full", tmp_path / "a")
    b = generate(WorldConfig(seed=99, **TINY), "full", tmp_path / "b")
    assert a.run_id == b.run_id
    assert a.events == b.events > 5_000
    assert a.events_digest == b.events_digest
    assert a.truth_digest == b.truth_digest
    for name in ("events.jsonl.gz", "ground_truth.json", "manifest.json"):
        assert _sha(tmp_path / "a" / name) == _sha(tmp_path / "b" / name), name


def test_replay_yields_identical_expected_outcomes(tmp_path):
    a = generate(WorldConfig(seed=5, **TINY), "core")
    b = generate(WorldConfig(seed=5, **TINY), "core")
    assert [g.to_dict() for g in a.ground_truth] == [g.to_dict() for g in b.ground_truth]


def test_different_seed_changes_dataset():
    a = generate(WorldConfig(seed=1, **TINY), "core")
    b = generate(WorldConfig(seed=2, **TINY), "core")
    assert a.events_digest != b.events_digest
    assert a.run_id != b.run_id


def test_run_id_depends_on_specs_and_config():
    w = WorldConfig(seed=1, **TINY)
    assert run_id_for(w, "core") == run_id_for(WorldConfig(seed=1, **TINY), "core")
    assert run_id_for(w, "core") != run_id_for(w, "full")
    assert run_id_for(w, "core") != run_id_for(WorldConfig(seed=1, scale=0.04), "core")


def test_scenarios_are_local_prefix_before_first_injection_is_identical():
    """Injecting scenarios must not perturb the world before they start (stream isolation)."""
    w = WorldConfig(seed=77, **TINY)
    first_fx = min(e.start for s in build_catalog(w, "core") for e in s.effects)
    assert first_fx == w.start + 2 * DAY + 14 * HOUR
    lines: dict[str, list[str]] = {"baseline": [], "core": []}
    for preset in lines:
        generate(w, preset, on_event=lambda ev, line, p=preset: lines[p].append(line) if ev.created < first_fx else None)
    assert len(lines["baseline"]) > 1000
    assert lines["baseline"] == lines["core"]
