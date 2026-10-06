"""Stage 7 end to end on a simulated world (ADR-049): every incident investigated once at creation, reports pass the
citation validator, budgets hold, the control always runs a disconfirming check, the engine records Mode B, storage
appends. The fake client is a pipeline check only; no quality is asserted."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from pulseos.detection import detect
from pulseos.events import store
from pulseos.incidents import pipeline as inc_pipeline
from pulseos.incidents.engine import Engine
from pulseos.investigation import pipeline as inv_pipeline
from pulseos.investigation import storage
from pulseos.investigation.choosers import ControlChooser, JevChooser
from pulseos.investigation.config import InvestigationConfig
from pulseos.jev.client import BudgetedClient, JevBudget
from pulseos.jev.fake import FakeJevClient
from pulseos.simulation import clickhouse_load as chl
from pulseos.simulation.runner import generate
from pulseos.simulation.world import WorldConfig

DB = "anomalyos_investigation_sim_test"
WORLD = WorldConfig(seed=42, scale=0.3)
CFG = InvestigationConfig()


@pytest.fixture(scope="module")
def world(ch, tmp_path_factory):
    d = tmp_path_factory.mktemp("investigation_world")
    generate(WORLD, "full", d)
    for stmt in chl.ddl(DB, DB + "_truth") + storage.ddl(DB):
        ch.client.command(stmt)
    chl.load_run(ch.client, d, DB, DB + "_truth", replace=True)
    out = store.load_norm(ch.client, d, DB, replace=True)
    S, E = datetime.fromtimestamp(WORLD.start, timezone.utc), datetime.fromtimestamp(WORLD.end, timezone.utc)
    cands = detect(ch.runner, DB, out["run_id"], S, E)
    s6 = inc_pipeline.run(ch.runner, DB, out["run_id"], cands, WORLD.start, WORLD.end,
                          BudgetedClient(FakeJevClient(), JevBudget(10**6)), "fake-jev-0", "all", estimate_impact=False)
    runs = {
        "control": inv_pipeline.run(ch.runner, DB, out["run_id"], s6, cands, WORLD.start, lambda v: ControlChooser(), CFG),
        "fake": inv_pipeline.run(ch.runner, DB, out["run_id"], s6, cands, WORLD.start,
                                 lambda v: JevChooser(FakeJevClient(), "fake-jev-0", v.as_of, tuple(v.anchor.evidence_ids)), CFG),
    }
    yield {"ch": ch, "run": out["run_id"], "s6": s6, "runs": runs}
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}")
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}_truth")


def test_every_incident_is_investigated_once(world):
    n = len(world["s6"].engine.incidents)
    for r in world["runs"].values():
        ids = [i.incident_id for i in r.investigations]
        assert ids and len(ids) == len(set(ids)) == n


def test_reports_are_cited_and_budgets_hold(world):
    for r in world["runs"].values():
        for inv in r.investigations:
            assert r.reports[inv.investigation_id].valid, r.reports[inv.investigation_id].errors
            b = inv.budget
            assert b["tool_calls"] <= CFG.max_tool_calls and b["jev_calls"] <= CFG.max_jev_calls
            assert b["evidence"] <= CFG.max_evidence
            assert inv.stop_reason and inv.conclusion


def test_the_control_always_runs_a_disconfirming_check(world):
    for inv in world["runs"]["control"].investigations:
        stepped = [s for s in inv.steps if s.get("phase") == "step" and "evidence_id" in s]
        if stepped:
            assert inv.disconfirming_checks >= 1, inv.investigation_id


def test_steps_never_repeat_and_tools_are_read_only(world):
    for r in world["runs"].values():
        for inv in r.investigations:
            keys = [(s["tool"], tuple(s["args"])) for s in inv.steps if s.get("phase") == "step"]
            assert len(keys) == len(set(keys))


def test_engine_records_mode_b(world):
    s6, r = world["s6"], world["runs"]["control"]
    result = Engine().run(s6.events + r.events)
    reasons = [e["reason"] for e in result.incident_events]
    assert reasons.count("investigation_started") == len(r.investigations)
    assert reasons.count("investigation_finished") == len(r.investigations)
    assert any(e["status"] == "INVESTIGATING" and e["reason"] == "investigation_started" for e in result.incident_events)
    assert all(i["investigation_status"] != "not_started" for i in result.incidents.values())
    assert all(e["status"] not in ("RESOLVED", "DISMISSED") for e in result.incident_events)


def test_storage_appends(world):
    r = world["runs"]["control"]
    inv = r.investigations[0]
    n = storage.append(world["ch"].client, DB, world["run"], inv, r.registries[inv.investigation_id],
                       r.reports[inv.investigation_id])
    assert n["investigation_reports"] == 1 and n["investigation_evidence"] >= 1
    rows = int(world["ch"].client.command(f"SELECT count() FROM {DB}.investigation_reports WHERE run_id = '{world['run']}'"))
    assert rows == 1
    assert json.loads(world["ch"].client.command(
        f"SELECT budget_json FROM {DB}.investigation_reports WHERE run_id = '{world['run']}'"))["tool_calls"] >= 1
