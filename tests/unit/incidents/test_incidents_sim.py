"""Stage 6 end to end on a simulated world (fake client — no quality asserted for Jev): every decided candidate is
accounted for, no system closure, replay reproduces the rows, impact is labelled, storage appends, and the campaign +
outage records of correlated_unrelated_anomalies never share an incident. Ground truth is read only by the test."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

from pulseos.detection import detect
from pulseos.evaluation.decisions import status
from pulseos.events import store
from pulseos.incidents import pipeline, storage
from pulseos.incidents.engine import Engine
from pulseos.incidents.lifecycle import TERMINAL
from pulseos.jev.client import BudgetedClient, JevBudget
from pulseos.jev.fake import FakeJevClient
from pulseos.simulation import clickhouse_load as chl
from pulseos.simulation.runner import generate
from pulseos.simulation.world import WorldConfig

DB = "anomalyos_incidents_sim_test"
WORLD = WorldConfig(seed=42, scale=0.3)


@pytest.fixture(scope="module")
def world(ch, tmp_path_factory):
    d = tmp_path_factory.mktemp("incidents_world")
    res = generate(WORLD, "full", d)
    for stmt in chl.ddl(DB, DB + "_truth") + storage.ddl(DB):
        ch.client.command(stmt)
    chl.load_run(ch.client, d, DB, DB + "_truth", replace=True)
    out = store.load_norm(ch.client, d, DB, replace=True)
    S, E = datetime.fromtimestamp(WORLD.start, timezone.utc), datetime.fromtimestamp(WORLD.end, timezone.utc)
    cands = detect(ch.runner, DB, out["run_id"], S, E)
    client = BudgetedClient(FakeJevClient(), JevBudget(max_calls=100_000))
    runs = {src: pipeline.run(ch.runner, DB, out["run_id"], cands, WORLD.start, WORLD.end, client, "fake-jev-0", src)
            for src in ("all", "baseline")}
    yield {"ch": ch, "run": out["run_id"], "cands": cands, "runs": runs, "truth": [g.to_dict() for g in res.ground_truth]}
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}")
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}_truth")


def test_route_agnostic_run_links_every_decided_candidate_once(world):
    r = world["runs"]["all"]
    linked = [c for inc in r.engine.incidents.values() for c in inc["linked_anomalies"]]
    decided = {i for i in r.infos}
    assert len(linked) == len(set(linked)) and set(linked) == decided and r.engine.digest_items == []


def test_no_system_transition_reaches_a_terminal_state(world):
    for r in world["runs"].values():
        assert all(e["status"] not in TERMINAL for e in r.engine.incident_events)
        assert all(e["actor"] in ("system", "policy") and e["reason"] for e in r.engine.incident_events)
    assert any(i["status"] == "RECOVERING" for i in world["runs"]["all"].engine.incidents.values())


def test_checkpoints_are_bounded_and_replay_reproduces_the_rows(world):
    r = world["runs"]["all"]
    per = {}
    for e in r.events:
        if e.kind == "checkpoint":
            per[e.candidate_id] = per.get(e.candidate_id, 0) + 1
    assert per and max(per.values()) <= 3
    again = Engine().run(r.events)
    no_impact = [e for e in r.engine.incident_events if e["reason"] != "impact_estimated"]
    assert again.incident_events == no_impact and again.links == r.engine.links


def test_impact_is_labelled(world):
    imps = [json.loads(e["snapshot_json"]).get("estimated_impact") for e in world["runs"]["all"].engine.incident_events
            if e["reason"] == "impact_estimated"]
    assert imps and all(i["lost_revenue_minor"]["epistemic"] == "ESTIMATED" and
                        i["captured_revenue_minor"]["epistemic"] == "OBSERVED" for i in imps)
    assert any(i["lost_successful_payments"] for i in imps)


def test_campaign_and_outage_never_share_an_incident(world):
    recs = world["truth"]
    full = {c.anomaly_id: dataclasses.asdict(c) for c in world["cands"]}
    by_incident_id = {r["incident_id"]: r["record_key"] for r in recs if r["incident_id"]}
    # unrelated_to holds incident ids of co-occurring, causally unrelated records
    pairs = {(r["record_key"], by_incident_id.get(o, o)) for r in recs
             if r["scenario_kind"] == "correlated_unrelated_anomalies" for o in r["unrelated_to"]}
    assert pairs
    for r in world["runs"].values():
        for inc in r.engine.incidents.values():
            matched = [status(full[c], recs, WORLD.end)[1] for c in inc["linked_anomalies"]]
            keys = {m["record_key"] for m in matched if m}
            for a, b in pairs:
                assert not (a in keys and b in keys), (inc["incident_id"], a, b)


def test_storage_appends_the_run(world):
    n = storage.append(world["ch"].client, DB, world["run"], world["runs"]["baseline"].engine)
    assert n["incident_events"] == len(world["runs"]["baseline"].engine.incident_events)
