"""End to end on a simulated world: Stage 3 candidates + sweep -> cohort analysis -> bundles.
Scenario windows come from the catalog docs (day N = start + N days), never from ground truth (INV-015)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from anomalyos.cohorts import analyze_candidates, sweep
from anomalyos.detection import detect
from anomalyos.events import store
from anomalyos.simulation import clickhouse_load as chl
from anomalyos.simulation.runner import generate
from anomalyos.simulation.world import WorldConfig

DB = "anomalyos_cohort_test"
WORLD = WorldConfig(seed=42, scale=0.5)
START = datetime.fromtimestamp(WORLD.start, timezone.utc)
END = datetime.fromtimestamp(WORLD.end, timezone.utc)


def day(n, h=0):
    return int((START + timedelta(days=n, hours=h)).timestamp())


@pytest.fixture(scope="module")
def world(ch, tmp_path_factory):
    d = tmp_path_factory.mktemp("cohort_world")
    generate(WORLD, "full", d)
    for stmt in chl.ddl(DB, DB + "_truth"):
        ch.client.command(stmt)
    chl.load_run(ch.client, d, DB, DB + "_truth", replace=True)
    out = store.load_norm(ch.client, d, DB, replace=True)
    cands = detect(ch.runner, DB, out["run_id"], START, END)
    swept = sweep(ch.runner, DB, out["run_id"], START, END)
    results = analyze_candidates(ch.runner, DB, out["run_id"], cands + swept, WORLD.start)
    yield {"ch": ch, "run": out["run_id"], "cands": cands, "sweep": swept, "results": results}
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}")
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}_truth")


def _find(results, **kw):
    return [(a, b) for a, b in results if all(getattr(a, k) == v for k, v in kw.items())]


def test_scoped_psp_outage_keeps_its_scope_as_locus(world):
    # simultaneous_incidents: psp_alpha cards x0.7 on day 5 (psp_alpha carries only cards)
    hits = [(a, b) for a, b in world["results"] if a.metric == "authorization_rate" and dict(a.scope) == {"psp": "psp_alpha"}
            and a.window[0] < day(5, 14) and day(5, 10) < a.window[1]]
    assert hits and all(a.label == "rate_change" and a.locus_is_scope for a, _ in hits)


def test_sepa_outage_is_localized_to_the_method(world):
    hits = [a for a, _ in world["results"] if a.window[0] < day(6, 22) and day(6, 18) < a.window[1]
            and a.metric == "authorization_rate" and a.locus and dict(a.locus).get("payment_method_type") == "sepa_debit"]
    assert hits


def test_healthy_demand_is_labelled_volume_only(world):
    # harmless_seasonality: BR demand x2.2 from day 20 12:00; approval unchanged
    hits = [a for a, _ in world["results"] if a.metric == "attempt_volume" and a.window[0] < day(21) and day(20, 12) < a.window[1]
            and a.locus and dict(a.locus).get("customer_country") == "BR"]
    assert hits and all(a.label == "volume_only" for a in hits)


def test_sweep_finds_cohort_drops_after_warm_up_only_in_sweep_combinations(world):
    sw = world["sweep"]
    assert sw and all(c.detected_at > day(3) and c.series_key == "S12" for c in sw)
    assert all({d for d, _ in c.scope} in ({"psp", "customer_country"}, {"customer_country", "payment_method_type"}) for c in sw)
    assert any(dict(c.scope) == {"customer_country": "DE", "payment_method_type": "sepa_debit"} for c in sw)


def test_bundles_are_bounded_typed_and_deterministic(world):
    bundles = [b for _, b in world["results"]]
    assert bundles and max(len(json.dumps(b)) for b in bundles) <= 8192
    for b in bundles:
        assert b["label"]["epistemic"] == "inferred" and b["decomposition"].get("epistemic") == "observed"
        assert b["impact"] is None or b["impact"]["epistemic"] == "estimated"
        assert len(b["top_cohorts"]) <= 5 and len(b["controls"]) <= 3 and len(b["related_metrics"]) <= 2
        assert abs(b["decomposition"].get("reconciliation_error", 0.0)) < 1e-9
    ch = world["ch"]
    again = analyze_candidates(ch.runner, DB, world["run"], world["cands"][:5], WORLD.start)
    first = {b["bundle_id"]: b for _, b in world["results"]}
    for _, b in again:
        assert b["bundle_id"] in first and b["top_cohorts"] == first[b["bundle_id"]]["top_cohorts"]
