"""sim-1.3.0 side files (ADR-049 D-3a): honest signals only for flagged faults, decoys, no truth leakage, as-of readers."""

from __future__ import annotations

import json

from pulseos.context import readers
from pulseos.simulation import clickhouse_load as chl
from pulseos.simulation.ground_truth import RootCause
from pulseos.simulation.scenarios import build_catalog
from pulseos.simulation.side_files import generate, validate
from pulseos.simulation.validate import validate_side_files
from pulseos.simulation.world import WorldConfig

W = WorldConfig(seed=5, scale=0.05)


def test_side_files_are_deterministic_and_valid():
    a, b = generate(W, build_catalog(W, "full")), generate(W, build_catalog(W, "full"))
    assert a.deployments == b.deployments and a.psp_status == b.psp_status and a.honest == b.honest
    assert validate(a, [c.value for c in RootCause]) == []
    assert len(a.deployments) > 20 and len(a.psp_status) > 5  # routine deploys, maintenance and minor entries exist


def test_only_flagged_fault_effects_get_honest_signals():
    harmless = {"harmless_seasonality", "small_cohort_noisy_anomaly", "ambiguous_signal", "benign_shock"}
    seen = set()
    for w in (W, WorldConfig(seed=5, scale=0.05, realism="v2")):  # benign shocks exist only in realism v2
        specs = build_catalog(w, "full")
        side = generate(w, specs)
        flagged = {e.effect_id for s in specs for e in s.effects if e.params.get("side_signal")}
        assert side.honest and set(side.honest) <= flagged
        for s in specs:
            if s.kind in harmless:
                seen.add(s.kind)
                assert not any(e.params.get("side_signal") for e in s.effects), s.kind
                assert not any(e.effect_id in side.honest for e in s.effects), s.kind
    assert seen == harmless  # the check is not vacuous


def test_entries_carry_no_truth_fields():
    side = generate(W, build_catalog(W, "full"))
    keys = {k for d in side.deployments for k in d} | {k for s in side.psp_status for k in s}
    assert keys <= {"id", "service", "version", "deployed_at", "psp", "component", "level", "posted_at", "resolved_at"}


def test_run_dir_has_side_files_and_truth_cites_them(full_run):
    d = full_run["dir"]
    dep = json.loads((d / "deployments.json").read_text())["deployments"]
    sts = json.loads((d / "psp_status.json").read_text())["psp_status"]
    gt = json.loads((d / "ground_truth.json").read_text())["records"]
    assert validate_side_files(gt, dep, sts).ok
    assert any(g["side_signals"] for g in gt)
    assert all(not g["side_signals"] for g in gt if g["expected_route"] != "incident")


class _Runner:
    def __init__(self, s):
        self.s = s

    def rows(self, sql, params):
        def lit(v):
            if isinstance(v, (list, tuple)):
                return "[" + ",".join("'" + str(x) + "'" for x in v) + "]"
            return str(v)
        out = self.s.query(sql, "JSONCompact", params={k: lit(v) for k, v in params.items()})
        return [tuple(r) for r in json.loads(out.bytes().decode())["data"]]


def test_loader_and_as_of_readers(full_run, tmp_path):
    import pytest
    pytest.importorskip("chdb", reason="optional: embedded ClickHouse")
    from tests.unit.simulation.sim_chdb_client import ChdbClient
    c = ChdbClient(str(tmp_path / "ch"))
    try:
        out = chl.load_run(c, full_run["dir"], "anomalyos")
        assert out["side_files"]["deployments"] > 0 and out["side_files"]["psp_status"] > 0
        run, w = out["run_id"], full_run["res"].manifest["window"]
        r = _Runner(c.s)
        everything = readers.deployments(r, "anomalyos", run, readers.SERVICES, w["start"], w["end"], w["end"])
        assert everything and all(w["start"] <= d.deployed_at <= w["end"] for d in everything)
        mid = w["start"] + (w["end"] - w["start"]) // 2
        early = readers.deployments(r, "anomalyos", run, readers.SERVICES, w["start"], w["end"], mid)
        assert all(d.deployed_at <= mid for d in early) and len(early) < len(everything)  # nothing after as_of
        sts = readers.psp_status(r, "anomalyos", run, readers.PSPS, w["start"], w["end"], mid)
        assert all(s.posted_at <= mid and (s.resolved_at is None or s.resolved_at <= mid) for s in sts)
        for bad in (("deploy_bot",), ()):
            with pytest.raises(ValueError):
                readers.deployments(r, "anomalyos", run, bad, w["start"], w["end"], mid)
    finally:
        c.close()
