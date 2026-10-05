"""Live ClickHouse: load a small run, normalize it, every SQL check is 0, reload is idempotent."""

from __future__ import annotations

import os

import pytest

from pulseos.events import store
from pulseos.simulation.runner import generate
from pulseos.simulation.world import WorldConfig

pytestmark = pytest.mark.integration
if os.environ.get("PULSEOS_RUN_INTEGRATION") != "1":
    pytest.skip("set PULSEOS_RUN_INTEGRATION=1", allow_module_level=True)


def test_normalize_live(tmp_path):
    from pulseos.simulation.clickhouse_load import connect, load_run, target_from_settings

    generate(WorldConfig(seed=2026, scale=0.02), "full", tmp_path / "run")
    t = target_from_settings()
    client = connect(t)
    load_run(client, tmp_path / "run", t.database, t.truth_database, replace=True)
    out = store.load_norm(client, tmp_path / "run", t.database, replace=True)
    assert out["checks"] and all(v == 0 for v in out["checks"].values()), out["checks"]
    assert store.load_norm(client, tmp_path / "run", t.database, replace=True)["norm_rows"] == out["norm_rows"]
