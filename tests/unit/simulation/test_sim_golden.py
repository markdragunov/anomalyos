"""Golden digests: any change to generator output must be deliberate (docs/DECISIONS.md, sim-1.x).

A failure here means the event stream or ground truth changed. If that is intended, bump
`GENERATOR_VERSION`, update the pinned values in a separate commit and explain what changed
in the commit message. Values were taken from sim-1.0.0 and reproduced on a second machine
(x86_64 / Python 3.13 and aarch64 / Python 3.10 and 3.12).
"""

from __future__ import annotations

import pytest

from anomalyos.simulation.ground_truth import GENERATOR_VERSION
from anomalyos.simulation.runner import generate
from anomalyos.simulation.world import WorldConfig

SEED5_SMALL = dict(
    run_id="run_3e694f661a80226d", events=68_429,
    events_sha256="c883137194271c77f43ccc18107e6241558d6abba836dcbbd2ced6802b0dba9f",
    truth_digest="aebaf8d1511807492caf49de98abacb8")

SEED42_FULL = dict(
    run_id="run_133c4a3a198eb8c1", events=1_366_081,
    events_sha256="1ac6d592589287acb536ad4d212928a9eec78cc3c0fba9cda3335417cece2a22",
    truth_digest="79bfe1fdeb0644de2c94842ec96588bb")


def _observed(res):
    return dict(run_id=res.run_id, events=res.events, events_sha256=res.events_digest, truth_digest=res.truth_digest)


def test_golden_digest_seed5_scale_005():
    res = generate(WorldConfig(seed=5, scale=0.05), "full")
    assert GENERATOR_VERSION == "sim-1.0.0", "generator version changed: update the golden values deliberately (separate commit)"
    assert _observed(res) == SEED5_SMALL


@pytest.mark.slow
def test_golden_digest_seed42_scale_1():
    res = generate(WorldConfig(seed=42, scale=1.0), "full")
    assert _observed(res) == SEED42_FULL
