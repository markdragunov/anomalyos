"""Score Stage 7 investigations against ground truth (ADR-049 D-9). Rules fixed at Gate 0, before results.

Input: investigations with their registries and reports, the incident views, each engine incident's main record (the
Stage 6 rule, ``evaluation.incidents``), the Stage 4 first-look bundles, ground-truth records. Output: one row per
investigation and the definitions below. Only evaluation code reads ground truth (INV-015).

* **Hypotheses:** the main record's ``true_cause`` at top-1 / top-3 of the final ranking — incident records only.
* **Contradiction kept:** when the truth is not the leader, the leader carries ≥ 1 contradicting evidence id.
* **Side files:** an honest entry of the record (``side_signals``) appears in a side-file evidence that supports some
  hypothesis; a **decoy** supports the leader when a present side entry backs it and none of its ids is honest.
* **Narrowing:** the root-cause locus or an affected cohort (``evaluation.cohorts.equivalent_exact``) is among the
  narrowed cohorts; the same test on the anchor's Stage 4 ``top_cohorts`` (k = 5) is the comparison.
* **False-positive incidents** (no main record, or a ``suppress`` / ``normal_variation`` main record): concluded
  ``normal_variation``, ``unknown`` or ``insufficient_evidence``.
* **Safety:** valid report, no repeated step, budgets held, a disconfirming check when ≥ 1 step ran, no tool outside
  the registry. **Cost:** steps, tool calls, Jev calls, evidence items, measured seconds.
"""

from __future__ import annotations

from typing import Any, Mapping

from pulseos.evaluation.cohorts import equivalent_exact
from pulseos.investigation.hypotheses import initial, prior, ranking
from pulseos.investigation.tools import REGISTRY

NOT_A_CAUSE = ("normal_variation", "unknown", "insufficient_evidence")


def _truth_row(main: Mapping[str, Any] | None) -> tuple[str, bool]:
    if main is None:
        return "none", True
    fp = main["expected_route"] == "suppress" or main["true_cause"] == "normal_variation"
    return main["true_cause"], fp


def _locus_hit(dims, record, metric) -> bool:
    return bool(record) and equivalent_exact(dims, record, metric)


def prior_only(view) -> list[str]:
    pri: dict[str, int] = {}
    for m in view.members:  # the same member-wide prior as the loop (ADR-050)
        for cause, w in prior(m.metric, m.direction, {d for d, _ in m.locus}, m.change_type).items():
            pri[cause] = max(pri.get(cause, 0), w)
    hs = initial("prior", pri, 5)
    return [h.cause for h in ranking(hs)]


def score(inv, registry, report, view, main: Mapping[str, Any] | None, bundle: Mapping[str, Any] | None, cfg,
          seconds: float | None = None) -> dict[str, Any]:
    truth, fp = _truth_row(main)
    order = [h.cause for h in ranking(inv.hypotheses)]
    lead = ranking(inv.hypotheses)[0] if inv.hypotheses else None
    steps = [s for s in inv.steps if s.get("phase") == "step" and "evidence_id" in s]
    keys = [(s["tool"], tuple(s["args"])) for s in inv.steps if s.get("phase") == "step"]
    audit = next((s["tool_calls"] for s in inv.steps if s.get("phase") == "audit"), [])
    honest = set((main or {}).get("side_signals") or [])
    supports = {e for h in inv.hypotheses for e in h.supporting}
    side_supporting = [registry.get(e) for e in supports if registry.get(e) and registry.get(e).tool in
                       ("get_deployments", "check_psp_status")]
    cited_honest = any(honest & set(e.payload.get("ids", [])) for e in side_supporting)
    lead_side = [registry.get(e) for e in (lead.supporting if lead else []) if registry.get(e)
                 and registry.get(e).tool in ("get_deployments", "check_psp_status")]
    decoy = any(e.payload.get("ids") and not (honest & set(e.payload["ids"])) for e in lead_side)
    metric = view.anchor.metric
    narrowed_hit = any(_locus_hit(registry.get(e).payload["cohort"], main, metric) for e in inv.narrowed
                       if registry.get(e) and main)
    stage4_hit = any(_locus_hit(t["cohort"], main, metric) for t in ((bundle or {}).get("top_cohorts") or [])[:5]) if main else False
    b = inv.budget
    return {
        "investigation_id": inv.investigation_id, "incident_id": inv.incident_id, "truth": truth,
        "scenario_kind": (main or {}).get("scenario_kind"), "false_positive_incident": fp,
        "top1": (not fp) and order[:1] == [truth], "top3": (not fp) and truth in order[:3],
        "leader": order[0] if order else None, "conclusion": inv.conclusion, "stop_reason": inv.stop_reason,
        "contradiction_kept": (not fp and order[:1] != [truth]) and bool(lead and lead.contradicting),
        "truth_not_leader": (not fp) and order[:1] != [truth],
        "fp_concluded_not_a_cause": fp and inv.conclusion in NOT_A_CAUSE,
        "has_side_signals": bool(honest), "cited_honest": cited_honest, "decoy_supports_leader": decoy,
        "narrowed_hit": narrowed_hit, "stage4_top5_hit": stage4_hit, "has_main": main is not None,
        "report_valid": report.valid, "repeats": len(keys) - len(set(keys)),
        "over_budget": b.get("tool_calls", 0) > cfg.max_tool_calls or b.get("jev_calls", 0) > cfg.max_jev_calls
        or b.get("evidence", 0) > cfg.max_evidence,
        "disconfirming_ok": (not steps) or inv.disconfirming_checks >= 1,
        "foreign_tools": sum(1 for a in audit if a["tool"] not in REGISTRY),
        "steps": len(steps), "tool_calls": b.get("tool_calls", 0), "jev_calls": b.get("jev_calls", 0),
        "evidence": b.get("evidence", 0), "seconds": seconds,
    }


def score_prior(view, main: Mapping[str, Any] | None) -> dict[str, Any]:
    truth, fp = _truth_row(main)
    order = prior_only(view)
    return {"incident_id": view.incident_id, "truth": truth, "false_positive_incident": fp,
            "scenario_kind": (main or {}).get("scenario_kind"), "top1": (not fp) and order[:1] == [truth],
            "top3": (not fp) and truth in order[:3], "leader": order[0],
            "fp_concluded_not_a_cause": fp and order[0] in NOT_A_CAUSE}
