"""Verifier (ADR-039 D-6): every malformed response class is rejected with its reason; nothing is repaired."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pulseos.jev.client import DecisionContext, JevError, make_request
from pulseos.jev.fake import FakeJevClient
from pulseos.jev.state import build_state
from pulseos.jev.verifier import verify
from tests.unit.jev.fixtures import bundle, candidate

PIN = "fake-jev-0"


@pytest.fixture
def case():
    s, prov = build_state(candidate(), bundle())
    req = make_request(s, PIN)
    ctx = DecisionContext(as_of=candidate()["detected_at"], evaluated_at=candidate()["detected_at"])
    resp = FakeJevClient(error_share=0.0, malformed_share=0.0).ask(req, ctx)
    return req, resp, prov, ctx


def _with(resp, answers):
    return replace(resp, answers=tuple(answers))


def test_well_formed_response_passes(case):
    req, resp, prov, ctx = case
    v = verify(req, resp, prov, ctx, PIN)
    assert v.ok and set(v.answers) == {"is_incident", "severity", "category", "needs_human"}


def _reasons(case, **change):
    req, resp, prov, ctx = case
    return verify(req, replace(resp, **change), prov, ctx, PIN).reasons


def test_each_malformed_class_is_rejected(case):
    req, resp, prov, ctx = case
    a = list(resp.answers)
    noul = next(i for i, (q, _) in enumerate(a) if q == "is_incident")
    choice = next(i for i, (q, _) in enumerate(a) if q == "severity")
    sev = a[choice][1]
    cases = {
        "missing_question": a[1:],
        "duplicate_question": a + a[:1],
        "unknown_question": a + [("is_recovering", {"p": 0.4})],
        "out_of_range": [(q, {"p": 1.3}) if i == noul else (q, p) for i, (q, p) in enumerate(a)],
        "invalid_option": [(q, {"probs": dict(sev["probs"], catastrophic=0.0), "confidence": 0.5}) if i == choice else (q, p)
                           for i, (q, p) in enumerate(a)],
        "cardinality": [(q, {"probs": {"low": 1.0}, "confidence": 0.5}) if i == choice else (q, p) for i, (q, p) in enumerate(a)],
        "inconsistent_choice": [(q, {"probs": {k: 0.5 for k in sev["probs"]}, "confidence": 0.5}) if i == choice else (q, p)
                                for i, (q, p) in enumerate(a)],
        "schema": [(q, {"p": 0.4, "why": "x"}) if i == noul else (q, p) for i, (q, p) in enumerate(a)],
    }
    for reason, answers in cases.items():
        v = verify(req, _with(resp, answers), prov, ctx, PIN)
        assert not v.ok and reason in v.reasons and v.answers is None, reason


def test_identity_and_metadata_checks(case):
    assert "state_hash_mismatch" in _reasons(case, state_hash="0" * 64)
    assert "schema_version_mismatch" in _reasons(case, state_schema_version="jev_state_v0")
    assert "question_set_mismatch" in _reasons(case, question_set_version="question_set_v0")
    assert "model_mismatch" in _reasons(case, returned_model="fake-jev-1")
    assert "model_mismatch" in _reasons(case, returned_model=None)
    req, resp, prov, ctx = case
    assert "stale_response" in _reasons(case, response_at=ctx.evaluated_at + ctx.freshness_s + 1)
    assert "oversized_response" in _reasons(case, raw_bytes=10 ** 6)


def test_missing_provenance_and_client_errors(case):
    req, resp, prov, ctx = case
    v = verify(req, resp, dict(prov, impact=()), ctx, PIN)
    assert not v.ok and "missing_provenance" in v.reasons
    for kind in ("timeout", "provider", "replay_miss", "budget_exhausted", "not_configured"):
        v = verify(req, JevError(kind, "x", req.request_hash, "t"), prov, ctx, PIN)
        assert not v.ok and v.reasons == (kind,)
