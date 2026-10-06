"""The loop on stubbed tools and narrowing (ADR-049 D-2, D-5): budgets hard-stop, no repeated step, model failure
stops, low confidence hands off, contradicting evidence kept, nothing raises."""

from __future__ import annotations

import pytest

from pulseos.incidents.engine import CandidateInfo
from pulseos.investigation import loop, tools
from pulseos.investigation.choosers import ControlChooser, ModelFailure
from pulseos.investigation.config import InvestigationConfig
from pulseos.investigation.evidence import Evidence, Registry
from pulseos.investigation.narrowing import Narrowed

H = 3600
ANCHOR = CandidateInfo("anom_a", "authorization_rate", "down", 0, frozenset({("psp", "b"), ("customer_country", "DE")}),
                       "INCIDENT", "dec_a", "t", "policy_v1", change_type="rate_change", window_end=H,
                       scope=frozenset({("psp", "b")}), evidence_ids=("evd_first",))
VIEW = tools.IncidentView("inc_1", H, "approval down", 0, (ANCHOR,), ANCHOR, "1h")


@pytest.fixture
def stubs(monkeypatch):
    calls = []

    def fake_call(ctx, name, *args):
        ctx.calls += 1
        calls.append((name, args))
        out = {"compare_cohorts": "unchanged", "get_related_metrics": "moved"}.get(name, "moved")
        payload = {"metric": "authorization_rate", "outcome": out, "present": False, "components": [],
                   "incident_id": "inc_1", "started_at": 0, "detected_at": H, "locus": [], "lost_successful_payments": 5.0}
        e = Evidence(f"evd_{name}_{len(calls)}", name, args, payload, {"outcome": "inferred", "metric": "observed",
                     "lost_successful_payments": "estimated"}, H)
        return ctx.registry.add(e)
    monkeypatch.setattr(tools, "call", fake_call)
    monkeypatch.setattr(loop, "narrow", lambda *a, **k: Narrowed([], 1, {"psp": ["a"], "customer_country": ["FR"]}, 1))
    return calls


def _ctx(cfg=InvestigationConfig()):
    return tools.ToolContext(None, "db", "run_x", 0, VIEW, Registry(), cfg)


def test_control_run_completes_with_a_disconfirming_check(stubs):
    inv = loop.investigate(VIEW, _ctx(), ControlChooser(), lambda: 0.0)
    assert inv.stop_reason in loop.STOP_REASONS and inv.disconfirming_checks >= 1
    keys = [(n, a) for n, a in stubs if n not in ("get_incident", "search_similar_incidents", "calculate_impact")]
    assert len(keys) == len(set(keys))  # no repeated step
    assert any(h.contradicting for h in inv.hypotheses)  # contradicting evidence is recorded


def test_tool_budget_is_a_hard_stop(monkeypatch, stubs):
    def ambiguous(ctx, name, *args):  # outcomes that move no hypothesis, so only the budget can stop the loop
        ctx.calls += 1
        e = Evidence(f"evd_{name}_{ctx.calls}", name, args, {"outcome": "ambiguous", "metric": "m", "present": False,
                     "components": []}, {}, H)
        return ctx.registry.add(e)
    monkeypatch.setattr(tools, "call", ambiguous)
    cfg = InvestigationConfig(max_tool_calls=3)
    inv = loop.investigate(VIEW, _ctx(cfg), ControlChooser(), lambda: 0.0)
    assert inv.stop_reason == "budget_exhausted" and inv.budget["tool_calls"] <= 3


def test_wall_clock_budget(stubs):
    t = iter(range(0, 10_000, 100))
    inv = loop.investigate(VIEW, _ctx(), ControlChooser(), lambda: float(next(t)))
    assert inv.stop_reason == "budget_exhausted" and inv.budget["detail"] == "wall_clock"


class _Chooser(ControlChooser):
    def __init__(self, mode):
        super().__init__()
        self.mode, self.name = mode, "stub"

    def choose(self, state, shortlist):
        if self.mode == "fail":
            raise ModelFailure("schema")
        return shortlist[0], 0.2

    def hypothesis_probs(self, state, causes):
        return None


def test_model_failure_stops_and_low_confidence_hands_off(stubs):
    assert loop.investigate(VIEW, _ctx(), _Chooser("fail"), lambda: 0.0).stop_reason == "model_failure"
    assert loop.investigate(VIEW, _ctx(), _Chooser("low"), lambda: 0.0).stop_reason == "low_confidence"


def test_a_tool_failure_never_raises(monkeypatch, stubs):
    def broken(ctx, name, *args):
        ctx.calls += 1
        raise tools.ToolError("down")
    monkeypatch.setattr(tools, "call", broken)
    inv = loop.investigate(VIEW, _ctx(), ControlChooser(), lambda: 0.0)
    assert inv.stop_reason == "tool_failure" and inv.conclusion == "unknown"
