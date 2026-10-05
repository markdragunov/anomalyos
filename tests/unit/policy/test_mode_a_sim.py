"""Mode A end to end on a simulated world with the fake client (ADR-039): one audited decision per decidable
candidate, first-look inputs only, bounded states, failures as DIGEST. The fake is not a model: no quality is asserted.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

import pytest

from pulseos.detection import detect
from pulseos.events import store
from pulseos.jev.client import BudgetedClient, JevBudget
from pulseos.jev.fake import FakeJevClient
from pulseos.jev.state import MAX_STATE_BYTES
from pulseos.policy import audit, baseline, mode_a
from pulseos.jev.state import JevState
from pulseos.simulation import clickhouse_load as chl
from pulseos.simulation.runner import generate
from pulseos.simulation.world import WorldConfig

DB = "anomalyos_mode_a_test"
WORLD = WorldConfig(seed=42, scale=0.3)
START = datetime.fromtimestamp(WORLD.start, timezone.utc)
END = datetime.fromtimestamp(WORLD.end, timezone.utc)


@pytest.fixture(scope="module")
def world(ch, tmp_path_factory):
    d = tmp_path_factory.mktemp("mode_a_world")
    generate(WORLD, "full", d)
    for stmt in chl.ddl(DB, DB + "_truth") + audit.ddl(DB):
        ch.client.command(stmt)
    chl.load_run(ch.client, d, DB, DB + "_truth", replace=True)
    out = store.load_norm(ch.client, d, DB, replace=True)
    cands = detect(ch.runner, DB, out["run_id"], START, END)
    client = BudgetedClient(FakeJevClient(), JevBudget(max_calls=10_000))
    results = mode_a.run(ch.runner, DB, out["run_id"], cands, WORLD.start, client, "fake-jev-0")
    yield {"ch": ch, "run": out["run_id"], "cands": cands, "results": results}
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}")
    ch.client.command(f"DROP DATABASE IF EXISTS {DB}_truth")


def test_one_decision_per_decidable_candidate(world):
    ids = [c.anomaly_id for c in mode_a.decidable(world["cands"])]
    assert ids and [d.record.candidate_id for d in world["results"]] == ids
    assert len({d.record.decision_id for d in world["results"]}) == len(ids)


def test_decisions_use_first_look_inputs_only(world):
    for d in world["results"]:
        r, v, b = d.record, d.candidate, d.bundle
        assert v.window_end == v.detected_at == r.as_of and v.recovered_at is None
        if b is not None:
            assert b["candidate"]["window"] == [v.window_start, v.detected_at]


def test_states_are_bounded_and_failures_are_digest(world):
    recs = [d.record for d in world["results"]]
    sizes = [len(r.state_json.encode()) for r in recs]
    assert max(sizes) <= MAX_STATE_BYTES
    for r in recs:
        assert r.route in ("IGNORE", "DIGEST", "INCIDENT")
        if not r.verified:
            assert r.route == "DIGEST" and r.verifier_reasons
    routes = Counter(r.route for r in recs)
    assert routes["DIGEST"] > 0  # the fake injects failures


def test_baseline_runs_on_the_same_states(world):
    for r in (d.record for d in world["results"]):
        if r.state_json != "{}":
            s = json.loads(r.state_json)
            state = JevState(**{k: tuple(v) if isinstance(v, list) else v for k, v in s.items()})
            assert baseline.route(state)[0] in ("IGNORE", "DIGEST", "INCIDENT")


def test_every_decision_is_audited(world):
    recs = [d.record for d in world["results"]]
    assert audit.append(world["ch"].client, DB, world["run"], recs) == len(recs)
    n = int(world["ch"].client.command(f"SELECT count() FROM {DB}.jev_decisions WHERE run_id = '{world['run']}'"))
    assert n == len(recs)
