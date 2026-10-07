"""Stage 7 evaluation rules (ADR-049 D-9) on a hand-built investigation."""

from __future__ import annotations

from pulseos.evaluation.investigation import score, score_prior
from pulseos.explanation.report import Report
from pulseos.incidents.engine import CandidateInfo
from pulseos.investigation.config import InvestigationConfig
from pulseos.investigation.evidence import Evidence, Registry
from pulseos.investigation.hypotheses import initial
from pulseos.investigation.loop import Investigation
from pulseos.investigation.tools import IncidentView

A = CandidateInfo("anom_a", "authorization_rate", "down", 0, frozenset({("psp", "b")}), "INCIDENT", "d", "t", "p",
                  change_type="rate_change", scope=frozenset({("psp", "b")}))
VIEW = IncidentView("inc_1", 3600, "t", 0, (A,), A, "1h")
REC = {"record_key": "r", "true_cause": "psp_degradation", "expected_route": "incident", "scenario_kind": "k",
       "root_cause": {"locus": {"psp": ["b"]}}, "affected_cohorts": [{"psp": ["b"]}], "side_signals": ["sts_honest"]}


def _inv(leader="psp_degradation"):
    reg = Registry()
    reg.add(Evidence("evd_s", "check_psp_status", (), {"ids": ["sts_honest"], "components": ["authorization"]}, {}, 3600))
    reg.add(Evidence("evd_n", "narrowing", (), {"cohort": [["psp", "b"]]}, {}, 3600))
    inv = Investigation("inv_1", "inc_1", 3600, "control", hypotheses=initial("inv_1", {"psp_degradation": 3,
                        "issuer_or_country_degradation": 1}, 5), narrowed=["evd_n"], budget={"tool_calls": 5})
    lead = next(h for h in inv.hypotheses if h.cause == leader)
    lead.supporting += ["evd_s", "evd_s2"]
    inv.steps = [{"phase": "step", "evidence_id": "evd_s", "tool": "check_psp_status", "args": ["b"]}]
    inv.disconfirming_checks = 1
    return inv, reg


def test_scores_hypotheses_side_signals_narrowing_and_safety():
    inv, reg = _inv()
    row = score(inv, reg, Report("inv_1"), VIEW, REC, {"top_cohorts": [{"cohort": [["psp", "b"]]}]}, InvestigationConfig())
    assert row["top1"] and row["top3"] and row["cited_honest"] and not row["decoy_supports_leader"]
    assert row["narrowed_hit"] and row["stage4_top5_hit"] and row["report_valid"] and row["repeats"] == 0
    assert not row["over_budget"] and row["disconfirming_ok"]


def test_decoy_and_false_positive_incidents():
    inv, reg = _inv()
    decoy = dict(REC, side_signals=["sts_other"])
    assert score(inv, reg, Report("inv_1"), VIEW, decoy, None, InvestigationConfig())["decoy_supports_leader"]
    inv.conclusion = "insufficient_evidence"
    fp = score(inv, reg, Report("inv_1"), VIEW, None, None, InvestigationConfig())
    assert fp["false_positive_incident"] and fp["fp_concluded_not_a_cause"] and not fp["top1"]
    assert score_prior(VIEW, REC)["top1"]
