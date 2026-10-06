"""Who picks the next step (ADR-019 option 3, ADR-049 D-1, D-2, D-4): the deterministic control, or Jev over a typed
state — through the ``JevClient`` port, so the fake (OQ-1 open), replay or the live transport.

The Jev chooser builds bounded typed states (closed-enum buckets, ids, no free text, no numbers), verifies every answer
with the Stage 5 verifier and raises ``ModelFailure`` on any unverified answer — the loop stops; it never falls back
to the control within a run (D-5), so the two systems stay separable.
"""

from __future__ import annotations

from pulseos.jev import buckets
from pulseos.jev.client import DecisionContext, JevClient, make_generic_request
from pulseos.jev.questions import (INVESTIGATION_QUESTION_SET_VERSION, chunk_question, hypothesis_question,
                                   step_question)
from pulseos.jev.verifier import verify
from pulseos.investigation.narrowing import Item, control_importance

STATE_SCHEMA = "investigation_state_v1"
MAX_STATE_BYTES = 2048


class ModelFailure(RuntimeError):
    """Jev's answer could not be verified."""


class ControlChooser:
    name = "control"

    def __init__(self) -> None:
        self.jev_calls = 0

    def importance(self, chunks: list[list[Item]]) -> list[float]:
        return control_importance(chunks)

    def choose(self, state: dict, shortlist) -> tuple[object | None, float]:
        return (shortlist[0] if shortlist else None), 1.0

    def hypothesis_probs(self, state: dict, causes: list[str]) -> dict[str, float] | None:
        return None


def _chunk_state(chunks: list[list[Item]]) -> dict:
    total = sum(abs(i.contribution) for c in chunks for i in c) or 1.0
    return {"chunks": [{"dims": sorted({d for i in c for d, _ in i.dims}),
                        "share": buckets.share(sum(abs(i.contribution) for i in c) / total),
                        "strength": buckets.strength(max((abs(i.z) for i in c if i.z is not None), default=None)),
                        "support": buckets.sample_size(sum(i.support for i in c)),
                        "size": buckets.cohorts_moved(len(c))} for c in chunks]}


class JevChooser:
    def __init__(self, client: JevClient, pinned_model: str, as_of: int, provenance_ids: tuple[str, ...]) -> None:
        self.client, self.model = client, pinned_model
        self.ctx = DecisionContext(as_of=as_of, evaluated_at=as_of)
        self.ids = provenance_ids or ("evd_incident",)
        self.name = client.name
        self.jev_calls = 0

    def _ask(self, state: dict, questions) -> dict:
        req = make_generic_request(state, tuple(questions), STATE_SCHEMA, INVESTIGATION_QUESTION_SET_VERSION, self.model)
        if req.size_bytes > 16 * MAX_STATE_BYTES:
            raise ModelFailure("state_too_large")
        self.jev_calls += 1
        out = self.client.ask(req, self.ctx)
        v = verify(req, out, {k: self.ids for k in state}, self.ctx, self.model)
        if not v.ok:
            raise ModelFailure(",".join(v.reasons))
        return v.answers

    def importance(self, chunks: list[list[Item]]) -> list[float]:
        a = self._ask(_chunk_state(chunks), [chunk_question(i) for i in range(len(chunks))])
        return [a[f"chunk_{i}"]["p"] for i in range(len(chunks))]

    def choose(self, state: dict, shortlist) -> tuple[object | None, float]:
        ids = tuple(c.check_id for c in shortlist)
        a = self._ask(state, [step_question(ids)])["next_step"]
        pick = max(a["probs"], key=lambda k: (a["probs"][k], k))
        return (None if pick == "stop" else next(c for c in shortlist if c.check_id == pick)), a["confidence"]

    def hypothesis_probs(self, state: dict, causes: list[str]) -> dict[str, float] | None:
        a = self._ask(state, [hypothesis_question(c) for c in causes])
        return {c: a[f"supports_{c}"]["p"] for c in causes}
