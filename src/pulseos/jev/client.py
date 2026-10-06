"""The ``JevClient`` port (ADR-024, ADR-039 D-4, D-10): typed request in, typed response or structured error out.

Implementations: ``fake.FakeJevClient`` (deterministic, not a model), ``replay.ReplayJevClient`` (recorded responses),
``transport.HttpJevClient`` (the only network I/O; not configured until provider access exists). ``BudgetedClient``
guards every call before it reaches an implementation. No implementation retries.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from pulseos.jev.questions import QUESTION_SET_VERSION, QUESTIONS_V1, Question
from pulseos.jev.state import STATE_SCHEMA_VERSION, JevState, canonical_json, state_dict, state_hash

ERROR_KINDS = ("timeout", "transport", "provider", "replay_miss", "budget_exhausted", "not_configured",
               "oversized_request")


@dataclass(frozen=True)
class DecisionContext:
    """Explicit time inputs (ADR-039 D-2); nothing in Stage 5 reads the clock. Replay and fake: evaluated_at = as_of."""
    as_of: int
    evaluated_at: int
    freshness_s: int = 900


@dataclass(frozen=True)
class JevRequest:
    question_set_version: str
    questions: tuple[Question, ...]
    state: dict[str, Any]
    state_schema_version: str
    state_hash: str
    requested_model: str

    @property
    def request_hash(self) -> str:
        body = {"question_set": self.question_set_version, "questions": [q.id for q in self.questions],
                "state_hash": self.state_hash, "schema": self.state_schema_version, "model": self.requested_model}
        return hashlib.sha256(canonical_json(body)).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(canonical_json({"questions": [[q.id, q.kind, q.wording, list(q.options)] for q in self.questions],
                                   "state": self.state}))


def make_request(state: JevState, requested_model: str) -> JevRequest:
    return JevRequest(QUESTION_SET_VERSION, QUESTIONS_V1, state_dict(state), STATE_SCHEMA_VERSION, state_hash(state),
                      requested_model)


def make_generic_request(state: dict[str, Any], questions: tuple[Question, ...], schema_version: str,
                         question_set_version: str, requested_model: str) -> JevRequest:
    """A request for another question set over another typed state (Stage 7: ``investigation_question_set_v1``)."""
    digest = hashlib.sha256(schema_version.encode() + b"\n" + canonical_json(state)).hexdigest()
    return JevRequest(question_set_version, tuple(questions), state, schema_version, digest, requested_model)


@dataclass(frozen=True)
class JevResponse:
    request_hash: str
    state_hash: str
    state_schema_version: str
    question_set_version: str
    answers: tuple[tuple[str, Any], ...]  # (question id, payload) in provider order; duplicates stay visible
    returned_model: str | None
    provider_request_id: str | None
    transport: str
    request_at: int
    response_at: int
    raw_bytes: int


@dataclass(frozen=True)
class JevError:
    kind: str
    detail: str
    request_hash: str
    transport: str


class JevClient(Protocol):
    name: str

    def ask(self, request: JevRequest, ctx: DecisionContext) -> JevResponse | JevError: ...


@dataclass(frozen=True)
class JevBudget:
    """Provider limits and price are pending access (OQ-1); calls and sizes are enforced now."""
    max_calls: int
    max_request_bytes: int = 4096
    max_response_bytes: int = 16384


class BudgetedClient:
    """Refuses before the inner client is reached: no call once the budget is spent, no oversized request."""

    def __init__(self, inner: JevClient, budget: JevBudget) -> None:
        self.inner, self.budget, self.calls = inner, budget, 0
        self.name = inner.name

    def ask(self, request: JevRequest, ctx: DecisionContext) -> JevResponse | JevError:
        if self.calls >= self.budget.max_calls:
            return JevError("budget_exhausted", f"{self.calls} calls used", request.request_hash, self.name)
        if request.size_bytes > self.budget.max_request_bytes:
            return JevError("oversized_request", f"{request.size_bytes} bytes", request.request_hash, self.name)
        self.calls += 1
        return self.inner.ask(request, ctx)
