"""Incident lifecycle (ADR-028 transition table, ADR-041 D-4). Pure; the only place a status changes.

Only a ``human`` actor can reach ``RESOLVED`` or ``DISMISSED``; any other actor raises. Every accepted transition
becomes a new append-only row with actor and reason (written by the engine).
"""

from __future__ import annotations

OPEN = ("DETECTED", "INVESTIGATING", "ACKNOWLEDGED", "ESCALATED", "RECOVERING")
TERMINAL = ("RESOLVED", "DISMISSED")
ACTORS = ("system", "policy", "human")

# (from, to) -> actors allowed
ALLOWED: dict[tuple[str, str], frozenset[str]] = {}


def _allow(froms, to, actors):
    for f in froms:
        ALLOWED[(f, to)] = frozenset(actors)


_allow(("DETECTED",), "INVESTIGATING", ("system",))  # Mode B started (Stage 7)
_allow(("DETECTED", "INVESTIGATING"), "ACKNOWLEDGED", ("human",))
_allow(("ACKNOWLEDGED", "INVESTIGATING"), "ESCALATED", ("human", "policy"))
_allow(("DETECTED", "INVESTIGATING", "ACKNOWLEDGED", "ESCALATED"), "RECOVERING", ("system",))
_allow(("RECOVERING",), "INVESTIGATING", ("system",))
_allow(OPEN, "RESOLVED", ("human",))
_allow(OPEN, "DISMISSED", ("human",))


class TransitionError(ValueError):
    """A transition the ADR-028 table does not allow for this actor."""


def check(current: str, target: str, actor: str) -> None:
    if actor not in ACTORS:
        raise TransitionError(f"unknown actor {actor!r}")
    allowed = ALLOWED.get((current, target))
    if allowed is None:
        raise TransitionError(f"{current} -> {target} is not a lifecycle transition")
    if actor not in allowed:
        raise TransitionError(f"{current} -> {target} needs one of {sorted(allowed)}, not {actor!r}")
