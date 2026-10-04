"""Verifier (ADR-039 D-6): a pure check of a Jev response against the request it answers.

Input: the request, the response or error, the state's evidence provenance, the decision context, the pinned model.
Output: ``Verification`` — ``ok`` with the answers by question id, or ``unverified`` with structured reason codes.
It never repairs or infers an answer, never invents evidence, never calls Jev, never reads the clock or ground truth.
No identities across questions are checked (ADR-018): only consistency within each primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from anomalyos.jev.client import DecisionContext, JevError, JevRequest, JevResponse
from anomalyos.jev.state import NOT_PROVIDED

REASONS = ("schema", "missing_question", "duplicate_question", "unknown_question", "invalid_option", "out_of_range",
           "inconsistent_choice", "cardinality", "missing_provenance", "state_hash_mismatch", "schema_version_mismatch",
           "question_set_mismatch", "model_mismatch", "stale_response", "oversized_response")
CHOICE_TOLERANCE = 0.01


@dataclass(frozen=True)
class Verification:
    ok: bool
    reasons: tuple[str, ...]
    answers: dict[str, Any] | None


def _prob(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 0.0 <= x <= 1.0


def verify(request: JevRequest, outcome: JevResponse | JevError, provenance: Mapping[str, tuple[str, ...]],
           ctx: DecisionContext, pinned_model: str, max_response_bytes: int = 16384) -> Verification:
    if isinstance(outcome, JevError):
        return Verification(False, (outcome.kind,), None)
    r, reasons = outcome, []
    if r.state_hash != request.state_hash or r.request_hash != request.request_hash:
        reasons.append("state_hash_mismatch")
    if r.state_schema_version != request.state_schema_version:
        reasons.append("schema_version_mismatch")
    if r.question_set_version != request.question_set_version:
        reasons.append("question_set_mismatch")
    if not r.returned_model or r.returned_model != pinned_model:
        reasons.append("model_mismatch")
    if r.response_at < r.request_at or r.response_at - ctx.evaluated_at > ctx.freshness_s or r.request_at < ctx.as_of:
        reasons.append("stale_response")
    if r.raw_bytes > max_response_bytes:
        reasons.append("oversized_response")
    for field, value in request.state.items():
        if value not in (NOT_PROVIDED, [], ()) and not provenance.get(field):
            reasons.append("missing_provenance")
            break

    by_id: dict[str, Any] = {}
    questions = {q.id: q for q in request.questions}
    if not isinstance(r.answers, tuple) or not all(isinstance(a, tuple) and len(a) == 2 for a in r.answers):
        reasons.append("schema")
    else:
        for qid, payload in r.answers:
            if qid not in questions:
                reasons.append("unknown_question")
            elif qid in by_id:
                reasons.append("duplicate_question")
            else:
                by_id[qid] = payload
        if set(questions) - set(by_id):
            reasons.append("missing_question")
        for qid, payload in by_id.items():
            q = questions[qid]
            if not isinstance(payload, dict):
                reasons.append("schema")
            elif q.kind == "noul":
                if set(payload) != {"p"}:
                    reasons.append("schema")
                elif not _prob(payload["p"]):
                    reasons.append("out_of_range")
            else:
                probs, conf = payload.get("probs"), payload.get("confidence")
                if not isinstance(probs, dict) or set(payload) != {"probs", "confidence"}:
                    reasons.append("schema")
                    continue
                if set(probs) - set(q.options):
                    reasons.append("invalid_option")
                elif set(q.options) - set(probs):
                    reasons.append("cardinality")
                if not all(_prob(p) for p in probs.values()) or not _prob(conf):
                    reasons.append("out_of_range")
                elif abs(sum(probs.values()) - 1.0) > CHOICE_TOLERANCE:
                    reasons.append("inconsistent_choice")
    reasons = tuple(dict.fromkeys(reasons))  # stable order, no duplicates
    return Verification(not reasons, reasons, by_id if not reasons else None)
