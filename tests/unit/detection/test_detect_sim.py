"""End to end on a simulated world: SQL series -> detectors -> candidates. Windows come from the catalog docs,
never from ground truth (detection must not read it; INV-015)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pulseos.detection import detect
from pulseos.events import store
from pulseos.simulation import clickhouse_load as chl
from pulseos.simulation.runner import generate
from pulseos.simulation.world import WorldConfig

DB = "anomalyos_detect_test"
START = datetime(2026, 8, 3, tzinfo=timezone.utc)
END = START + timedelta(days=28)
H = 3600


@pytest.fixture(scope="module")
def world(ch, tmp_path_factory):
    d = tmp_path_factory.mktemp("detect_world")
    generate(WorldConfig(seed=42, scale=0.3), "full", d)
    for stmt in chl.ddl(DB, DB + "_truth"):
        ch.client.command(stmt)
    chl.load_run(ch.client, d, DB, DB + "_truth", replace=True)
    out = store.load_norm(ch.client, d, DB, replace=True)
    yield ch, out["run_id"]
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}")
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}_truth")


@pytest.fixture(scope="module")
def main_cands(world):
    ch, run = world
    return detect(ch.runner, DB, run, START, END)


def ts(days, hours=0):
    return int((START + timedelta(days=days, hours=hours)).timestamp())


def test_psp_degradation_is_detected_on_its_psp_within_the_incident(main_cands):
    s, e = ts(5, 10), ts(5, 14)  # simultaneous_incidents: psp_alpha cards x0.7 (fixed calendar)
    hits = [c for c in main_cands if c.series_key == "S2" and dict(c.scope).get("psp") == "psp_alpha"
            and c.window_start < e and s < c.window_end and c.direction == "down"]
    # At scale 0.3, 08:00-11:59 UTC is night for psp_alpha's US traffic: 20-29 attempts/hour, below the 30-attempt
    # floor (ADR-026), so the first decidable window is 12:00 and detection comes at 13:00, not at the oracle's 11:00.
    assert hits and min(c.detected_at for c in hits) <= e


def test_incidents_inside_the_warm_up_cannot_be_detected(main_cands):
    # scn_psp_auth_degradation sits on day 2 of the fixed calendar: fewer than two reference days exist, so no
    # baseline and no candidate. Reported as a separate miss reason in the evaluation, not hidden.
    s, e = ts(2, 14), ts(2, 17)
    assert not [c for c in main_cands if c.window_start < e and s < c.window_end]


def test_late_delivery_is_visible_at_first_look(main_cands):
    s, e = ts(3, 14), ts(3, 16)  # data_pipeline_issue: psp_gamma events delivered 1-2 h late
    late = [c for c in main_cands if c.window_start < e + 2 * H and s < c.window_end
            and (c.series_key == "S11" or (c.series_key == "S8" and dict(c.scope).get("psp") == "psp_gamma"))]
    assert late


def test_detection_is_deterministic_and_ids_unique(world, main_cands):
    ch, run = world
    again = detect(ch.runner, DB, run, START, END)
    assert again == main_cands
    assert len({c.anomaly_id for c in main_cands}) == len(main_cands)
    assert all(c.detected_at >= ts(3) for c in main_cands)  # warm-up: nothing in the first three days


def test_static_system_runs_on_the_same_series(world):
    ch, run = world
    static = detect(ch.runner, DB, run, START, END, system="static")
    assert static and all(c.method == ("static",) for c in static)
