"""Client port (ADR-039 D-4, D-5, D-10): fake determinism, replay round trip, budget guard, unconfigured transport."""

from __future__ import annotations

import json

from pulseos.jev.client import BudgetedClient, DecisionContext, JevBudget, JevError, make_request
from pulseos.jev.fake import FakeJevClient
from pulseos.jev.questions import CAUSES_V1
from pulseos.jev.replay import RecordingClient, ReplayJevClient, ReplayStore, redact
from pulseos.jev.state import build_state
from pulseos.jev.transport import HttpJevClient
from pulseos.jev.verifier import verify
from tests.unit.jev.fixtures import bundle, candidate

CTX = DecisionContext(as_of=candidate()["detected_at"], evaluated_at=candidate()["detected_at"])


def _req(**kw):
    s, prov = build_state(candidate(**kw), bundle())
    return make_request(s, "fake-jev-0"), prov


def test_fake_is_deterministic_and_exercises_failures():
    req, _ = _req()
    f = FakeJevClient()
    assert f.ask(req, CTX) == f.ask(req, CTX)
    kinds = {"ok": 0, "error": 0, "malformed": 0}
    import itertools
    from pulseos.jev.buckets import METRIC_FAMILY
    grid = itertools.product(sorted(METRIC_FAMILY), (-0.01, -0.03, -0.1, -0.3, -0.6), (3.0, 5.0, 9.0), (100, 1000, 9000))
    for metric, rel, z, n in grid:  # 495 distinct states -> 495 distinct request hashes
        r, prov = _req(metric=metric, relative_delta=rel, score_at_detection=z, sample_size=n)
        out = FakeJevClient(error_share=0.1, malformed_share=0.2).ask(r, CTX)
        if isinstance(out, JevError):
            kinds["error"] += 1
        else:
            kinds["ok" if verify(r, out, prov, CTX, "fake-jev-0").ok else "malformed"] += 1
    assert all(n > 0 for n in kinds.values()), kinds


def test_record_then_replay_reproduces_the_response(tmp_path):
    store = ReplayStore(tmp_path / "jev.jsonl")
    req, _ = _req()
    live = RecordingClient(FakeJevClient(error_share=0.0, malformed_share=0.0), store).ask(req, CTX)
    again = ReplayJevClient(ReplayStore(tmp_path / "jev.jsonl")).ask(req, CTX)
    assert again == live
    miss, _ = _req(sample_size=10)
    assert ReplayJevClient(store).ask(miss, CTX).kind == "replay_miss"


def test_secrets_are_redacted_before_persistence():
    assert redact({"api_key": "sk", "headers": {"Authorization": "Bearer x"}, "answers": [1]}) == \
        {"api_key": "[redacted]", "headers": {"Authorization": "[redacted]"}, "answers": [1]}


def test_budget_guard_refuses_before_the_client():
    calls = []

    class Spy(FakeJevClient):
        def ask(self, request, ctx):
            calls.append(request.request_hash)
            return super().ask(request, ctx)

    c = BudgetedClient(Spy(), JevBudget(max_calls=2))
    req, _ = _req()
    outs = [c.ask(req, CTX) for _ in range(3)]
    assert len(calls) == 2 and isinstance(outs[2], JevError) and outs[2].kind == "budget_exhausted"
    tiny = BudgetedClient(Spy(), JevBudget(max_calls=5, max_request_bytes=10))
    assert tiny.ask(req, CTX).kind == "oversized_request" and len(calls) == 2


def test_transport_is_not_configured_until_access_exists():
    req, _ = _req()
    out = HttpJevClient("https://example.invalid", "jev-x").ask(req, CTX)
    assert isinstance(out, JevError) and out.kind == "not_configured"


def test_cause_vocabulary_matches_the_simulator():
    from pulseos.simulation.ground_truth import RootCause
    assert set(CAUSES_V1) == {c.value for c in RootCause}


def test_request_is_small_and_hash_ignores_nothing_relevant():
    req, _ = _req()
    assert req.size_bytes < 4096
    other, _ = _req(score_at_detection=3.0)
    assert other.request_hash != req.request_hash and json.dumps(req.state)
