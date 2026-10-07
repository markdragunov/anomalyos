"""Golden digests: any change to generator output must be deliberate (docs/DECISIONS.md, sim-1.x).

A failure here means the event stream or ground truth changed. If that is intended, bump
`GENERATOR_VERSION`, update the pinned values in a separate commit and explain what changed
in the commit message. sim-1.0.0 values were reproduced on three machines (x86_64 / Python 3.13, aarch64 / Python 3.10 and 3.12);
sim-1.1.0 values come from aarch64 / Python 3.12 and are re-checked by CI (x86_64 / Python 3.11).
"""

from __future__ import annotations

import pytest

from pulseos.simulation.ground_truth import GENERATOR_VERSION
from pulseos.simulation.runner import generate
from pulseos.simulation.world import WorldConfig

# sim-1.3.0 (ADR-049: side files; event digests equal sim-1.2.2 — the run id and ground truth change: side_signals and
# the side_signal effect parameter). sim-1.2.2: seed 5 run_a76e806a05ee9ce0 / 1616e412...; seed 42 run_af277296736ca545 /
# ba760195.... sim-1.2.2 (event digests equal sim-1.2.0; ground truth differs in unchanged_metrics). sim-1.2.1: seed 5 run_43bec258f2017f19 / 1e0c6f92...; seed 42 run_75e402c8fdb77d3b / ea261229.... sim-1.2.0: seed 5 run_311ecbd4d37db101 / bb488a2f...; seed 42 run_b84548339e942431 / de48da75.... sim-1.1.1: seed 5 run_7f4755d4ca384dea / 47add09d... / c643e0f0...; seed 42 run_b50c96ff698d7675 / 2cf8a9b7... / 61520d2a.... sim-1.0.0 values, for the record: seed 5 run_3e694f661a80226d /
# c883137194271c77... / aebaf8d1...; seed 42 run_133c4a3a198eb8c1 / 1ac6d592589287ac... / 79bfe1fd...
SEED5_SMALL = dict(
    run_id="run_a132a84597d3e35f", events=68_541,
    events_sha256="65b120642d3b28f653eecc946c996e17cffae01f68aced4b1838e219102437f2",
    truth_digest="dddf9f4d13202927fb2041e79b068960", side_files_digest="ca5c3993e24d779a9c97c63285a50487")

SEED42_FULL = dict(
    run_id="run_b095557a44d5196b", events=1_368_114,
    events_sha256="6d369941d0fb5f86b88f1995a8f0b7eee05388007c0cee7f499676da42d9f17d",
    truth_digest="3ee7d9709932b4cd22c33ae5bcdcb82a", side_files_digest="f295c299e9ab25e85ba99573c1dd9a0b")


def _observed(res):
    return dict(run_id=res.run_id, events=res.events, events_sha256=res.events_digest, truth_digest=res.truth_digest,
                side_files_digest=res.manifest["side_files_digest"])


def test_golden_digest_seed5_scale_005():
    res = generate(WorldConfig(seed=5, scale=0.05), "full")
    assert GENERATOR_VERSION == "sim-1.3.0", "generator version changed: update the golden values deliberately (separate commit)"
    assert _observed(res) == SEED5_SMALL


@pytest.mark.slow
def test_golden_digest_seed42_scale_1():
    res = generate(WorldConfig(seed=42, scale=1.0), "full")
    assert _observed(res) == SEED42_FULL
