"""One Mode A decision end to end on hand-built inputs: every failure is DIGEST with an audit record, replay reproduces."""

from __future__ import annotations

import pytest

from pulseos.jev.client import BudgetedClient, DecisionContext, JevBudget, JevError
from pulseos.jev.fake import FakeJevClient
from pulseos.jev.replay import RecordingClient, ReplayJevClient, ReplayStore
from pulseos.jev.transport import HttpJevClient
from pulseos.policy import audit
from pulseos.policy.decide import decide
from tests.unit.jev.fixtures import bundle, candidate

CTX = DecisionContext(as_of=candidate()["detected_at"], evaluated_at=candidate()["detected_at"])
OK = FakeJevClient(error_share=0.0, malformed_share=0.0)


def test_verified_decision_has_a_complete_record():
    r = decide(candidate(), bundle(), [], OK, CTX, "fake-jev-0")
    assert r.verified and r.route in ("IGNORE", "DIGEST", "INCIDENT") and r.rule.startswith("r")
    assert r.state_hash and r.request_hash and r.calls_used == 1 and r.incident_p is not None
    assert "evd_first" in r.evidence_ids and "evd_top" in r.evidence_ids and r.actor == "mode_a_pipeline"
    assert r == decide(candidate(), bundle(), [], OK, CTX, "fake-jev-0")  # deterministic, stable decision id


class _Err:
    name = "stub"

    def __init__(self, kind):
        self.kind = kind

    def ask(self, request, ctx):
        return JevError(self.kind, "x", request.request_hash, self.name)


@pytest.mark.parametrize("client,reason", [
    (_Err("timeout"), "timeout"), (_Err("provider"), "provider"), (HttpJevClient(None, "m"), "not_configured"),
    (BudgetedClient(OK, JevBudget(max_calls=0)), "budget_exhausted"),
    (FakeJevClient(error_share=0.0, malformed_share=0.0, model="other"), "model_mismatch"),
])
def test_operational_failures_route_to_digest_and_are_audited(client, reason):
    r = decide(candidate(), bundle(), [], client, CTX, "fake-jev-0")
    assert r.route == "DIGEST" and r.rule == "r0_unverified" and reason in r.verifier_reasons and not r.verified
    assert r.decision_id and r.state_hash  # the failure itself is a record


def test_invalid_state_is_a_digest_record_without_a_call():
    r = decide(candidate(metric="mystery_rate"), None, [], OK, CTX, "fake-jev-0")
    assert r.route == "DIGEST" and r.verifier_reasons == ("state_invalid",) and r.calls_used == 0


def test_replay_reproduces_the_decision(tmp_path):
    store = ReplayStore(tmp_path / "r.jsonl")
    live = decide(candidate(), bundle(), [], RecordingClient(OK, store), CTX, "fake-jev-0")
    again = decide(candidate(), bundle(), [], ReplayJevClient(ReplayStore(tmp_path / "r.jsonl")), CTX, "fake-jev-0")
    fields = ("decision_id", "state_hash", "request_hash", "answers_json", "route", "rule", "verified")
    assert {f: getattr(live, f) for f in fields} == {f: getattr(again, f) for f in fields}
    miss = decide(candidate(sample_size=5), bundle(), [], ReplayJevClient(store), CTX, "fake-jev-0")
    assert miss.route == "DIGEST" and miss.verifier_reasons == ("replay_miss",) and miss.calls_used == 0


def test_audit_table_is_append_only(ch):
    db = "anomalyos_policy_test"
    for stmt in audit.ddl(db):
        ch.client.command(stmt)
    recs = [decide(candidate(anomaly_id=f"anom_{i}"), bundle(), [], OK, CTX, "fake-jev-0") for i in range(3)]
    assert audit.append(ch.client, db, "run_test", recs) == 3
    assert audit.append(ch.client, db, "run_test", recs[:1], first_seq=3) == 1  # a re-decision is a new row
    n = int(ch.client.command(f"SELECT count() FROM {db}.jev_decisions WHERE run_id = 'run_test'"))
    assert n == 4
    ch.client.command(f"DROP DATABASE IF EXISTS {db}")
