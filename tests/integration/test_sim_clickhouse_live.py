"""Opt-in: generate a small run, load it into the docker-compose ClickHouse, run SQL checks."""

from __future__ import annotations

import os

import pytest

from anomalyos.simulation.runner import generate
from anomalyos.simulation.world import WorldConfig

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("ANOMALYOS_RUN_INTEGRATION") != "1", reason="set ANOMALYOS_RUN_INTEGRATION=1"),
]


def test_generate_load_and_check(tmp_path):
    from anomalyos.simulation.clickhouse_load import connect, load_run, target_from_settings

    res = generate(WorldConfig(seed=2026, scale=0.02), "full", tmp_path / "run")
    t = target_from_settings()
    client = connect(t)
    out = load_run(client, tmp_path / "run", t.database, t.truth_database, replace=True)
    assert out["events"] == res.events
    assert all(v == 0 for v in out["checks"].values()), out["checks"]
    # Aggregation happens in ClickHouse, not Python.
    rows = client.query(
        f"SELECT psp, avg(ok) FROM {t.database}.v_charge_attempts WHERE run_id = %(r)s GROUP BY psp ORDER BY psp",
        parameters={"r": res.run_id},
    ).result_rows
    assert [r[0] for r in rows] == ["psp_alpha", "psp_beta", "psp_gamma"]
    assert all(0.5 < r[1] < 1.0 for r in rows)
