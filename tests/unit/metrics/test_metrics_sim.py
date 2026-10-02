"""Sanity on simulated data: the metrics see what the scenarios injected.

The windows come from the catalog documentation (docs/SIMULATION.md §5; day N = start + N days),
never from the truth database. Seeded, small scale; asserts are wide margins, not tuned point values.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from anomalyos.events import store
from anomalyos.metrics import compute
from anomalyos.simulation import clickhouse_load as chl

WORLD_START = datetime(2026, 8, 3, tzinfo=timezone.utc)
DB = "anomalyos_metrics_sim_test"


@pytest.fixture(scope="module")
def sim(ch, full_run):
    # the shared `ch` fixture owns a different database; load this run next to it
    for stmt in chl.ddl(DB, DB + "_truth"):
        ch.client.command(stmt)
    out_raw = chl.load_run(ch.client, full_run["dir"], DB, DB + "_truth", replace=True)
    out = store.load_norm(ch.client, full_run["dir"], DB, replace=True)
    assert all(v == 0 for v in out["checks"].values()), out["checks"]
    yield ch, out["run_id"]
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}")
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}_truth")


def total(points):
    n, d = sum(p.numerator for p in points), sum(p.denominator for p in points)
    return n, d, (n / d if d else None)


def series(sim, metric, start, end, grain, filters=None, group_by=()):
    ch, run = sim
    return compute(ch.runner, DB, run, metric, 1, start, end, grain, filters, group_by)


def test_psp_degradation_shows_in_authorization_rate_by_psp(sim):
    s = WORLD_START + timedelta(days=2, hours=14)  # scn_psp_auth_degradation: d2 14:00-17:00, psp_beta
    pts = series(sim, "authorization_rate", s, s + timedelta(hours=3), "1h", {"channel": "checkout"}, ("psp",))
    rate = {}
    for psp in ("psp_alpha", "psp_beta", "psp_gamma"):
        rate[psp] = total([p for p in pts if dict(p.dims)["psp"] == psp])[2]
    assert rate["psp_beta"] < min(rate["psp_alpha"], rate["psp_gamma"]) - 0.2, rate
    nxt = series(sim, "authorization_rate", s + timedelta(days=1), s + timedelta(days=1, hours=3), "1h", {"channel": "checkout", "psp": "psp_beta"})
    assert total(nxt)[2] > rate["psp_beta"] + 0.2  # same hours the day after: no degradation


def test_android_regression_lowers_conversion_but_not_authorization(sim):
    s, e = WORLD_START + timedelta(days=10), WORLD_START + timedelta(days=12)  # inside d9 -> hotfix d11 16:00 + decay
    bad = {"platform": "android", "app_version": "5.14.0"}
    ctl = {"platform": "ios", "app_version": "5.14.0"}
    conv_bad = total(series(sim, "checkout_conversion_rate", s, e, "1d", bad))[2]
    conv_ctl = total(series(sim, "checkout_conversion_rate", s, e, "1d", ctl))[2]
    auth_bad = total(series(sim, "authorization_rate", s, e, "1d", dict(bad, channel="checkout")))[2]
    auth_ctl = total(series(sim, "authorization_rate", s, e, "1d", dict(ctl, channel="checkout")))[2]
    assert conv_bad < conv_ctl - 0.25, (conv_bad, conv_ctl)
    assert auth_bad > auth_ctl - 0.08, (auth_bad, auth_ctl)  # charge approval is unaffected by this bug


def test_renewal_failure_scenario_is_visible_by_psp(sim):
    s, e = WORLD_START + timedelta(days=12), WORLD_START + timedelta(days=14)  # d12 -> d14 12:00 on psp_gamma cards
    pts = series(sim, "authorization_rate", s, e, "1d", {"channel": "renewal"}, ("psp",))
    rate = {psp: total([p for p in pts if dict(p.dims)["psp"] == psp])[2] for psp in ("psp_alpha", "psp_gamma")}
    assert rate["psp_gamma"] < rate["psp_alpha"] - 0.15, rate


def test_every_dimension_value_is_explicit_never_empty(sim):
    ch, run = sim
    for dim in ("psp", "card_brand", "platform", "issuer_country", "plan_id"):
        pts = series(sim, "attempt_volume", WORLD_START, WORLD_START + timedelta(days=1), "1d", group_by=(dim,))
        assert all(dict(p.dims)[dim] != "" for p in pts)


def test_global_series_is_dense_and_in_range(sim):
    pts = series(sim, "authorization_rate", WORLD_START, WORLD_START + timedelta(days=2), "1h")
    assert len(pts) == 48 and all(p.value is None or 0 <= p.value <= 1 for p in pts)
    assert all(p.is_complete for p in pts)
