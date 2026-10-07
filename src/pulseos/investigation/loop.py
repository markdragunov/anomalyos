"""The investigation loop (ADR-049 D-2, D-5, D-6): OBSERVE → NARROW → HYPOTHESES → SHORTLIST → CHOOSE → EXECUTE →
UPDATE → STOP / CONTINUE. Code owns every step; the chooser only picks among the shortlist.

Input: an ``IncidentView``, a ``ToolContext``, a chooser (control or Jev), a clock function (a step clock in tests and
evaluation; measured time only feeds the wall-clock budget and the audit, never a decision about content).
Output: an ``Investigation`` — steps, evidence registry, hypotheses, stop reason, conclusion, budgets used. Every stop
returns the partial structured state; nothing raises out of ``investigate``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

from pulseos.investigation import checks as checks_mod
from pulseos.investigation import tools
from pulseos.investigation.choosers import ModelFailure
from pulseos.investigation.config import InvestigationConfig
from pulseos.investigation.hypotheses import Hypothesis, initial, prior, ranking
from pulseos.investigation.narrowing import narrow
from pulseos.jev import buckets

STOP_REASONS = ("sufficient_evidence", "strong_contradiction", "false_positive", "budget_exhausted", "tool_failure",
                "model_failure", "low_confidence", "no_checks_left", "no_disconfirming_check")


@dataclass
class Investigation:
    investigation_id: str
    incident_id: str
    as_of: int
    chooser: str
    steps: list[dict] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    narrowed: list[str] = field(default_factory=list)  # evidence ids of the narrowed cohort items
    stop_reason: str = ""
    conclusion: str = "unknown"
    budget: dict = field(default_factory=dict)
    impact_evidence: str | None = None
    disconfirming_checks: int = 0


def _state(view, hyps: list[Hypothesis], shortlist, budget_left: int) -> dict:
    a = view.anchor
    return {
        "incident": {"metric_family": buckets.METRIC_FAMILY.get(a.metric, "other"), "direction": a.direction,
                     "locus_dims": sorted(d for d, _ in a.locus), "change_type": a.change_type or "not_provided"},
        "hypotheses": [{"cause": h.cause, "status": h.status, "supports": buckets.cohorts_moved(len(h.supporting)),
                        "contradicts": buckets.cohorts_moved(len(h.contradicting))} for h in ranking(hyps)],
        "shortlist": [{"id": c.check_id, "kind": c.kind, "tests": sorted({cs for cs, _ in c.predictions})} for c in shortlist],
        "budget_left": buckets.cohorts_moved(budget_left),
        "evidence": [{"cause": h.cause, "supports": len(h.supporting) > 0, "contradicts": len(h.contradicting) > 0}
                     for h in hyps],
    }


def investigate(view, ctx: tools.ToolContext, chooser, clock: Callable[[], float],
                cfg: InvestigationConfig | None = None) -> Investigation:
    cfg = cfg or ctx.cfg  # one configuration source: the budgets the tools are counted against
    inv = Investigation("inv_" + hashlib.sha256(f"{view.incident_id}|{view.as_of}|{cfg.version}|{chooser.name}".encode())
                        .hexdigest()[:16], view.incident_id, view.as_of, chooser.name)
    t0 = clock()
    failures = 0

    def over_budget() -> str | None:
        if ctx.calls >= cfg.max_tool_calls:
            return "tool_calls"
        if chooser.jev_calls >= cfg.max_jev_calls:
            return "jev_calls"
        if len(ctx.registry) >= cfg.max_evidence:
            return "evidence_items"
        if clock() - t0 > cfg.max_wall_s:
            return "wall_clock"
        return None

    def stop(reason: str, detail: str = "") -> Investigation:
        inv.stop_reason = reason
        for h in inv.hypotheses:
            if h.status == "open" and not h.supporting and not h.contradicting:
                h.status = "insufficient_evidence"
        lead = ranking(inv.hypotheses)[0] if inv.hypotheses else None
        if reason == "strong_contradiction" or lead is None:
            inv.conclusion = "unknown"
        elif reason == "false_positive":
            inv.conclusion = "normal_variation"
        else:
            inv.conclusion = lead.cause if lead.status == "supported" else "insufficient_evidence"
        if ctx.calls < cfg.max_tool_calls and len(ctx.registry) < cfg.max_evidence:  # impact, if the budget allows
            try:
                inv.impact_evidence = tools.call(ctx, "calculate_impact").evidence_id
            except tools.ToolError:
                inv.impact_evidence = None
        inv.budget = {"tool_calls": ctx.calls, "jev_calls": chooser.jev_calls, "evidence": len(ctx.registry),
                      "wall_s": round(clock() - t0, 3), "detail": detail}
        return inv

    # OBSERVE
    try:
        tools.call(ctx, "get_incident")
        tools.call(ctx, "search_similar_incidents")
    except tools.ToolError as e:
        return stop("tool_failure", str(e))
    # NARROW (Stage A)
    try:
        nar = narrow(view, ctx.runner, ctx.db, ctx.run_id, ctx.world_start, cfg, chooser.importance)
    except ModelFailure as e:
        return stop("model_failure", str(e))
    except Exception as e:  # a failed read in narrowing is a tool failure
        return stop("tool_failure", type(e).__name__)
    for item in nar.items:
        ctx.registry.add(tools.Evidence(item.evidence_id, "narrowing", (), {
            "cohort": [list(x) for x in item.dims], "contribution": round(item.contribution, 6),
            "z": None if item.z is None else round(item.z, 3), "support": item.support},
            {"cohort": "observed", "contribution": "observed", "z": "observed", "support": "observed"}, view.as_of))
    inv.narrowed = [i.evidence_id for i in nar.items]
    # ADR-050: the anchor's Stage 4 top cohorts always join the narrowed set, so it is never worse than Stage 4
    known = {i.dims for i in nar.items}
    for eid, dims in view.stage4_top:
        if dims not in known and len(ctx.registry) < cfg.max_evidence:
            ctx.registry.add(tools.Evidence(eid, "stage4_top", (), {"cohort": [list(x) for x in dims]},
                                            {"cohort": "observed"}, view.as_of))
            inv.narrowed.append(eid)
    inv.steps.append({"phase": "narrowing", "chooser": chooser.name, "importance_calls": nar.importance_calls,
                      "chunks_seen": nar.chunks_seen, "items": len(nar.items)})
    # HYPOTHESES
    a = view.anchor
    # ADR-050: priors from every member, not only the anchor (a renewal incident's anchor can be an approval member)
    pri: dict[str, int] = {}
    for m in view.members:
        for cause, w in prior(m.metric, m.direction, {d for d, _ in m.locus}, m.change_type).items():
            pri[cause] = max(pri.get(cause, 0), w)
    inv.hypotheses = initial(inv.investigation_id, pri, cfg.max_hypotheses)
    causes = [h.cause for h in inv.hypotheses]
    psps = sorted({v for d, v in a.locus if d == "psp"} | {v for d, v in a.scope if d == "psp"})
    platform = next((v for d, v in a.locus if d == "platform"), None)
    release = f"mobile_{platform}" if platform in ("ios", "android") else None
    library = checks_mod.build(a.metric, a.direction, dict(a.locus), nar.siblings, causes, release, psps)
    if not cfg.use_side_files:
        library = [c for c in library if c.kind not in ("deploy", "status")]
    done: set[tuple] = set()
    # LOOP
    while True:
        reason = over_budget()
        if reason:
            return stop("budget_exhausted", reason)
        hyps = ranking(inv.hypotheses)
        lead = hyps[0]
        prior_holders = [h for h in inv.hypotheses if h.prior > 0]
        if any(h.cause == "normal_variation" and h.status == "supported" for h in inv.hypotheses):
            return stop("false_positive")
        if prior_holders and all(h.status == "contradicted" for h in prior_holders):
            return stop("strong_contradiction")
        if lead.status == "supported" and (hyps[1].status == "contradicted" or lead.score - hyps[1].score >= 2):
            return stop("sufficient_evidence")
        sl = checks_mod.shortlist(library, done, inv.hypotheses, cfg.shortlist_size)
        if not sl:
            return stop("no_checks_left")
        if not any(checks_mod.disconfirming(c, lead) for c in sl):
            return stop("no_disconfirming_check")
        try:
            pick, confidence = chooser.choose(_state(view, inv.hypotheses, sl, cfg.max_tool_calls - ctx.calls), sl)
        except ModelFailure as e:
            return stop("model_failure", str(e))
        if pick is None:
            return stop("low_confidence", "stop chosen")
        if confidence < cfg.choice_confidence_min:
            return stop("low_confidence", f"confidence {confidence:.2f}")
        if pick.key in done:  # can only happen through a bug: never run a step twice
            return stop("tool_failure", "repeated step")
        done.add(pick.key)
        lead_before = lead.cause
        try:
            e = tools.call(ctx, pick.tool, *pick.args)
        except tools.ToolError as err:
            failures += 1
            inv.steps.append({"phase": "step", "check": pick.check_id, "tool": pick.tool, "args": list(map(str, pick.args)),
                              "error": str(err)})
            if failures >= 2:
                return stop("tool_failure", str(err))
            continue
        if pick.kind in ("deploy", "status"):  # side checks; sibling, channel and related checks carry an outcome
            p = e.payload
            observed = frozenset({f"present:{pick.args[0]}"} if p.get("present") else set()) if pick.kind == "deploy" \
                else frozenset(f"present:{c}" for c in p.get("components", []))
        else:
            observed = e.payload["outcome"]
        if checks_mod.disconfirming(pick, next(h for h in inv.hypotheses if h.cause == lead_before)):
            inv.disconfirming_checks += 1
        checks_mod.update(inv.hypotheses, pick, observed, e.evidence_id)
        for h in inv.hypotheses:
            h.next_question = None
        nxt = checks_mod.shortlist(library, done, inv.hypotheses, 1)
        if nxt:
            ranking(inv.hypotheses)[0].next_question = nxt[0].check_id
        inv.steps.append({"phase": "step", "check": pick.check_id, "kind": pick.kind, "tool": pick.tool,
                          "args": [str(x) for x in pick.args], "evidence_id": e.evidence_id,
                          "observed": sorted(observed) if isinstance(observed, frozenset) else observed,
                          "chooser": chooser.name, "confidence": round(confidence, 3), "leader_before": lead_before})
        if chooser.name != "control":
            try:
                probs = chooser.hypothesis_probs(_state(view, inv.hypotheses, [], cfg.max_tool_calls - ctx.calls),
                                                 [h.cause for h in inv.hypotheses])
            except ModelFailure as err:
                return stop("model_failure", str(err))
            for h in inv.hypotheses:
                h.support_probability = probs[h.cause] if probs else None
