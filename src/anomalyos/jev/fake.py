"""Deterministic fake Jev (ADR-039 D-5). **Not a model**: its answers say nothing about the candidate.

Answers are derived from SHA-256 of the request hash, so the same request always gets the same answer; a fixed share
of responses is malformed or fails, so every verifier and fallback path is exercised. Route results produced with it
are pipeline diagnostics only and must never be reported as a Jev evaluation.
"""

from __future__ import annotations

import hashlib
import json

from anomalyos.jev.client import DecisionContext, JevError, JevRequest, JevResponse

DEFECTS = ("missing_question", "duplicate_question", "unknown_question", "invalid_option", "out_of_range",
           "inconsistent_choice", "model_mismatch")


class FakeJevClient:
    name = "fake"

    def __init__(self, model: str = "fake-jev-0", error_share: float = 0.02, malformed_share: float = 0.05) -> None:
        self.model, self.error_share, self.malformed_share = model, error_share, malformed_share

    @staticmethod
    def _u(request_hash: str, key: str) -> float:
        return int(hashlib.sha256(f"{request_hash}|{key}".encode()).hexdigest()[:12], 16) / 16 ** 12

    def ask(self, request: JevRequest, ctx: DecisionContext) -> JevResponse | JevError:
        h = request.request_hash
        if self._u(h, "error") < self.error_share:
            return JevError("provider", "fake provider error", h, self.name)
        answers: list[tuple[str, object]] = []
        for q in request.questions:
            if q.kind == "noul":
                answers.append((q.id, {"p": round(self._u(h, q.id), 6)}))
            else:
                w = [self._u(h, f"{q.id}|{o}") ** 3 for o in q.options]
                probs = {o: round(x / sum(w), 6) for o, x in zip(q.options, w)}
                answers.append((q.id, {"probs": probs, "confidence": round(self._u(h, q.id + "|c"), 6)}))
        model = self.model
        if self._u(h, "malformed") < self.malformed_share:
            defect = DEFECTS[int(self._u(h, "defect") * len(DEFECTS))]
            answers, model = _break(answers, defect, model)
        return JevResponse(h, request.state_hash, request.state_schema_version, request.question_set_version,
                           tuple(answers), model, f"fake-{h[:12]}", self.name, ctx.evaluated_at, ctx.evaluated_at,
                           len(json.dumps(answers)))


def _break(answers: list, defect: str, model: str) -> tuple[list, str]:
    a = [(qid, json.loads(json.dumps(p))) for qid, p in answers]
    if defect == "missing_question":
        a = a[1:]
    elif defect == "duplicate_question":
        a = a + a[:1]
    elif defect == "unknown_question":
        a = a + [("is_recovering", {"p": 0.5})]
    elif defect == "invalid_option":
        choice = next(p for _, p in a if "probs" in p)
        choice["probs"]["catastrophic"] = choice["probs"].pop(next(iter(choice["probs"])))
    elif defect == "out_of_range":
        next(p for _, p in a if "p" in p)["p"] = 1.7
    elif defect == "inconsistent_choice":
        choice = next(p for _, p in a if "probs" in p)
        choice["probs"] = {o: 0.9 for o in choice["probs"]}
    elif defect == "model_mismatch":
        model = model + "-other"
    return a, model
