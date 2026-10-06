"""Lifecycle (ADR-028 table, ADR-041 D-4): every allowed transition, and nothing but a human closes."""

from __future__ import annotations

import pytest

from pulseos.incidents.lifecycle import ALLOWED, OPEN, TERMINAL, TransitionError, check


def test_table_matches_adr_028():
    assert ALLOWED[("DETECTED", "INVESTIGATING")] == {"system"}
    assert ALLOWED[("INVESTIGATING", "ACKNOWLEDGED")] == {"human"}
    assert ALLOWED[("ACKNOWLEDGED", "ESCALATED")] == {"human", "policy"}
    assert ALLOWED[("RECOVERING", "INVESTIGATING")] == {"system"}
    for s in OPEN:
        for t in TERMINAL:
            assert ALLOWED[(s, t)] == {"human"}
    assert all(f not in TERMINAL for f, _ in ALLOWED)  # terminal states have no way out


def test_only_humans_reach_terminal_states():
    for s in OPEN:
        for t in TERMINAL:
            check(s, t, "human")
            for actor in ("system", "policy"):
                with pytest.raises(TransitionError):
                    check(s, t, actor)


def test_forbidden_transitions_raise():
    for bad in (("DETECTED", "ESCALATED", "human"), ("RESOLVED", "DETECTED", "human"), ("DETECTED", "ACKNOWLEDGED", "system"),
                ("RECOVERING", "RECOVERING", "system"), ("DETECTED", "RECOVERING", "jev")):
        with pytest.raises(TransitionError):
            check(*bad)
