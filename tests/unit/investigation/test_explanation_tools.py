"""Citation validator (INV-008), template explainer and the tool registry (INV-007, ADR-049 D-3, D-7)."""

from __future__ import annotations

import pytest

from pulseos.explanation.report import Claim, Report, Slot, TemplateExplainer
from pulseos.explanation.validator import validate
from pulseos.investigation import tools
from pulseos.investigation.evidence import Evidence, Registry
from pulseos.investigation.hypotheses import initial
from pulseos.investigation.loop import Investigation

MUTATION = ("refund", "retry", "capture", "cancel", "disable", "page", "notify", "write", "update", "delete", "close",
            "resolve")


def _reg():
    r = Registry()
    r.add(Evidence("evd_inc", "get_incident", (), {"incident_id": "inc_1", "started_at": 0, "detected_at": 3600,
                                                   "locus": [["psp", "b"]]},
                   {"incident_id": "observed", "started_at": "observed", "detected_at": "observed", "locus": "inferred"}, 3600))
    r.add(Evidence("evd_m", "compare_cohorts", (), {"metric": "authorization_rate", "outcome": "moved", "z": -3.1},
                   {"metric": "observed", "outcome": "inferred", "z": "observed"}, 3600))
    r.add(Evidence("evd_imp", "calculate_impact", (), {"lost_successful_payments": 42.0},
                   {"lost_successful_payments": "estimated"}, 3600))
    return r


def test_validator_rejects_bad_citations():
    r = _reg()
    bad = Report("inv_1", [Claim("summary", (), "x"), Claim("against", (Slot("evd_nope", "metric", "observed"),), "x"),
                           Claim("against", (Slot("evd_m", "nofield", "observed"),), "x"),
                           Claim("against", (Slot("evd_m", "z", "estimated"),), "x"),
                           Claim("impact", (Slot("evd_imp", "lost_successful_payments", "observed"),), "x"),
                           Claim("against", (Slot("evd_m", "metric", "guess"),), "x")])
    errs = validate(bad, r).errors
    assert len(errs) == 6 and not bad.valid


def test_template_report_is_valid_and_cites_contradictions():
    r = _reg()
    inv = Investigation("inv_1", "inc_1", 3600, "control", hypotheses=initial("inv_1", {"psp_degradation": 3}, 5),
                        impact_evidence="evd_imp")
    inv.hypotheses[0].supporting.append("evd_m")
    inv.hypotheses[1].contradicting.append("evd_m")
    rep = validate(TemplateExplainer().explain(inv, r), r)
    assert rep.valid and any(c.template == "against" for c in rep.claims)
    assert all(c.slots for c in rep.claims) and not any("caused by" in c.text for c in rep.claims)
    assert any("42.0" in c.text for c in rep.claims if c.template == "impact")


def test_registry_has_no_mutation_tool_and_rejects_open_arguments():
    assert len(tools.REGISTRY) == 10
    assert not [n for n in tools.REGISTRY if any(m in n for m in MUTATION)]
    view = tools.IncidentView("inc_1", 3600, "t", 0, (), None, "1h")
    ctx = tools.ToolContext(None, "db", "run_x", 0, view, Registry(), None)
    with pytest.raises(tools.ToolError):
        tools.get_deployments(ctx, "deploy_bot", "onset_2h")
    with pytest.raises(tools.ToolError):
        tools.check_psp_status(ctx, "psp_zeta", "start_6h")
    with pytest.raises(tools.ToolError):
        tools.call(ctx, "issue_refund")
