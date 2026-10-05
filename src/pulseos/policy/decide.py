"""One Mode A decision (ADR-039): first-look candidate -> JevState -> client -> verifier -> policy_v1 -> record.

Input: the first-look candidate (``detection.as_of_view``), its Stage 4 bundle on the as-of window (or ``None``), the
candidates concurrent at detection, a ``JevClient`` (fake, replay or budgeted), the decision context, the pinned model.
Output: a ``DecisionRecord`` — always exactly one, including for failures, which route to ``DIGEST`` with a structured
reason and no retry (ADR-028). Pure apart from the client call; nothing here reads ground truth or the clock.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Iterable, Mapping

from pulseos.jev.assessment import Assessment, assess
from pulseos.jev.client import DecisionContext, JevClient, JevError, make_request
from pulseos.jev.questions import QUESTION_SET_VERSION
from pulseos.jev.state import STATE_SCHEMA_VERSION, StateError, build_state, canonical_json, state_dict
from pulseos.jev.verifier import verify
from pulseos.policy import rules
from pulseos.policy.config import PolicyConfig

ACTOR = "mode_a_pipeline"


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    mode: str
    candidate_id: str
    as_of: int
    evaluated_at: int
    state_json: str
    state_hash: str
    state_schema_version: str
    question_set_version: str
    requested_model: str
    returned_model: str
    transport: str
    request_hash: str
    provider_request_id: str
    answers_json: str  # verified answers by question id ("{}" when unverified)
    verified: bool
    verifier_reasons: tuple[str, ...]
    policy_version: str
    route: str
    rule: str
    evidence_ids: tuple[str, ...]
    provenance_json: str
    request_at: int
    response_at: int
    actor: str
    calls_used: int
    request_bytes: int
    incident_p: float | None = None
    severity_level: str = ""
    category_top: str = ""


def _d(x: Any) -> dict:
    return asdict(x) if is_dataclass(x) else dict(x)


def _decision_id(candidate_id: str, cfg: PolicyConfig, state_hash: str, as_of: int) -> str:
    return "dec_" + hashlib.sha256(f"{candidate_id}|{cfg.version}|{QUESTION_SET_VERSION}|{state_hash}|{as_of}".encode()).hexdigest()[:16]


def decide(candidate: Any, bundle: Mapping[str, Any] | None, concurrent: Iterable[Any], client: JevClient,
           ctx: DecisionContext, pinned_model: str, cfg: PolicyConfig = PolicyConfig()) -> DecisionRecord:
    c = _d(candidate)
    base = dict(mode="A", candidate_id=c["anomaly_id"], as_of=ctx.as_of, evaluated_at=ctx.evaluated_at,
                state_schema_version=STATE_SCHEMA_VERSION, question_set_version=QUESTION_SET_VERSION,
                requested_model=pinned_model, policy_version=cfg.version, actor=ACTOR)
    try:
        state, prov = build_state(c, bundle, concurrent)
    except StateError:
        return DecisionRecord(decision_id=_decision_id(c["anomaly_id"], cfg, "", ctx.as_of), state_json="{}",
                              state_hash="", returned_model="", transport=client.name, request_hash="",
                              provider_request_id="", answers_json="{}", verified=False,
                              verifier_reasons=("state_invalid",), route="DIGEST", rule="r0_unverified",
                              evidence_ids=tuple(c["evidence_ids"][:1]), provenance_json="{}", request_at=ctx.evaluated_at,
                              response_at=ctx.evaluated_at, calls_used=0, request_bytes=0, **base)
    request = make_request(state, pinned_model)
    outcome = client.ask(request, ctx)
    v = verify(request, outcome, prov, ctx, pinned_model)
    a: Assessment | None = assess(v.answers) if v.ok else None
    route, rule = rules.route(a, state, cfg)
    is_err = isinstance(outcome, JevError)
    called = not is_err or outcome.kind in ("timeout", "transport", "provider")  # a provider (or fake) was asked
    return DecisionRecord(
        decision_id=_decision_id(c["anomaly_id"], cfg, request.state_hash, ctx.as_of),
        state_json=canonical_json(state_dict(state)).decode(), state_hash=request.state_hash,
        returned_model="" if is_err else (outcome.returned_model or ""), transport=outcome.transport,
        request_hash=request.request_hash, provider_request_id="" if is_err else (outcome.provider_request_id or ""),
        answers_json=json.dumps(v.answers or {}, sort_keys=True), verified=v.ok, verifier_reasons=v.reasons,
        route=route, rule=rule, evidence_ids=tuple(sorted({e for ids in prov.values() for e in ids})),
        provenance_json=json.dumps({k: list(ids) for k, ids in prov.items()}, sort_keys=True),
        request_at=ctx.evaluated_at if is_err else outcome.request_at,
        response_at=ctx.evaluated_at if is_err else outcome.response_at,
        calls_used=int(called), request_bytes=request.size_bytes,
        incident_p=a.incident_p if a else None, severity_level=a.severity_level if a else "",
        category_top=a.category_top if a else "", **base)
