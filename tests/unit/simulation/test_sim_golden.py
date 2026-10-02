"""Golden digests: any change to generator output must be deliberate (docs/DECISIONS.md, sim-1.x).

A failure here means the event stream or ground truth changed. If that is intended, bump
`GENERATOR_VERSION`, update the pinned values in a separate commit and explain what changed
in the commit message. sim-1.0.0 values were reproduced on three machines (x86_64 / Python 3.13, aarch64 / Python 3.10 and 3.12);
sim-1.1.0 values come from aarch64 / Python 3.12 and are re-checked by CI (x86_64 / Python 3.11).
"""

from __future__ import annotations

import pytest

from anomalyos.simulation.ground_truth import GENERATOR_VERSION
from anomalyos.simulation.runner import generate
from anomalyos.simulation.world import WorldConfig

# sim-1.1.0 (ADR-029, ADR-030). sim-1.0.0 values, for the record: seed 5 run_3e694f661a80226d /
# c883137194271c77... / aebaf8d1...; seed 42 run_133c4a3a198eb8c1 / 1ac6d592589287ac... / 79bfe1fd...
SEED5_SMALL = dict(
    run_id="run_8bcb7384ca3c75fe", events=68_429,
    events_sha256="47add09d6ecd3661040d6f7ae61c80a3f26c7b587b829892bbd4479a80b2eb20",
    truth_digest="4c6e1f4c5c2b638e42cefba1b8557c37")

SEED42_FULL = dict(
    run_id="run_2ddebcdbac838013", events=1_366_081,
    events_sha256="2cf8a9b7901464de0bd2b48a5e49b8e98d39a9a92ed247cf5bdfd21188f8302a",
    truth_digest="2b818586c057feee2c08b34a8a448988")


def _observed(res):
    return dict(run_id=res.run_id, events=res.events, events_sha256=res.events_digest, truth_digest=res.truth_digest)


def test_golden_digest_seed5_scale_005():
    res = generate(WorldConfig(seed=5, scale=0.05), "full")
    assert GENERATOR_VERSION == "sim-1.1.0", "generator version changed: update the golden values deliberately (separate commit)"
    assert _observed(res) == SEED5_SMALL


@pytest.mark.slow
def test_golden_digest_seed42_scale_1():
    res = generate(WorldConfig(seed=42, scale=1.0), "full")
    assert _observed(res) == SEED42_FULL
