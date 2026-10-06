"""Investigate every incident once, at its creation (ADR-049 D-0), and hand the result back to the incident engine.

Input: a runner, database, run id, the Stage 6 ``PipelineResult`` (incident rows, events, infos), the Stage 3
candidates, world start, a chooser factory (control or Jev) and the configuration. Output: ``InvestigationRun`` — per
incident the investigation, its evidence registry and validated report, plus the engine events (`investigation` start
and finish) to fold into the incident history. Each view holds only what was known at ``as_of`` (members' infos as of
their latest detection / checkpoint event at or before it). The default clock is a step clock (each tool call costs a
fixed time), so runs are reproducible; nothing reads the system clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Iterable

from pulseos.detection.candidates import AnomalyCandidate
from pulseos.explanation.report import Report, TemplateExplainer
from pulseos.explanation.validator import validate
from pulseos.incidents import impact as impact_mod
from pulseos.incidents.engine import Event
from pulseos.investigation import tools
from pulseos.investigation.config import InvestigationConfig
from pulseos.investigation.evidence import Registry
from pulseos.investigation.loop import Investigation, investigate

SECONDS_PER_CALL = 5.0
STATUS = {"sufficient_evidence": "completed", "strong_contradiction": "completed", "false_positive": "completed",
          "no_checks_left": "completed", "no_disconfirming_check": "completed", "low_confidence": "handed_off",
          "budget_exhausted": "budget_exhausted", "tool_failure": "failed", "model_failure": "failed"}


@dataclass
class InvestigationRun:
    investigations: list[Investigation] = field(default_factory=list)
    registries: dict[str, Registry] = field(default_factory=dict)
    reports: dict[str, Report] = field(default_factory=dict)
    views: dict[str, tools.IncidentView] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)


def views_at_creation(stage6, candidates: Iterable[AnomalyCandidate], offset_s: int = 0) -> list[tools.IncidentView]:
    """Views at each incident's creation, or ``offset_s`` later (the evaluation's diagnostic pass, ADR-049 D-0)."""
    grain = {c.anomaly_id: c.grain for c in candidates}
    infos_by_time: dict[str, list] = {}
    for e in stage6.events:
        if e.kind in ("detected", "checkpoint"):
            infos_by_time.setdefault(e.candidate_id, []).append((e.time, e.info))
    first_rows: dict[str, dict] = {}
    for r in stage6.engine.incident_events:
        if r["incident_id"] not in first_rows or r["seq"] < first_rows[r["incident_id"]]["seq"]:
            first_rows[r["incident_id"]] = r
    out = []
    created = sorted(first_rows.values(), key=lambda r: (r["event_time"], r["incident_id"]))
    for row in created:
        as_of = row["event_time"] + offset_s
        rows_then = [r for r in stage6.engine.incident_events if r["incident_id"] == row["incident_id"] and r["event_time"] <= as_of]
        snap = json.loads(max(rows_then, key=lambda r: r["seq"])["snapshot_json"])
        members = []
        for cid in snap["linked_anomalies"]:
            known = [i for t, i in sorted(infos_by_time.get(cid, []), key=lambda x: x[0]) if t <= as_of]
            if known:
                members.append(known[-1])
        if not members:
            continue
        anchor = impact_mod.anchor(members) or max(members, key=lambda m: (len(m.locus), -m.start, m.candidate_id))
        earlier = []
        for other in created:
            if other["event_time"] < as_of:
                rows = [r for r in stage6.engine.incident_events
                        if r["incident_id"] == other["incident_id"] and r["event_time"] <= as_of]
                last = max(rows, key=lambda r: r["seq"])
                s = json.loads(last["snapshot_json"])
                first_member = s["linked_anomalies"][0]
                metric = next((i.metric for _, i in infos_by_time.get(first_member, [])), "")
                earlier.append({"incident_id": other["incident_id"], "status": last["status"], "started_at": s["started_at"],
                                "anchor_metric": metric, "locus_dims": [d for d, _ in s["affected_dimensions"]]})
        bundle = (getattr(stage6, "bundles", None) or {}).get(anchor.candidate_id) or {}
        top = tuple((t["evidence_id"], tuple(tuple(x) for x in t["cohort"])) for t in (bundle.get("top_cohorts") or [])[:5])
        out.append(tools.IncidentView(row["incident_id"], as_of, snap["title"], snap["started_at"], tuple(members), anchor,
                                      grain.get(anchor.candidate_id, "1h"), tuple(earlier), top))
    return out


def run(runner, db: str, run_id: str, stage6, candidates: Iterable[AnomalyCandidate], world_start: int,
        chooser_factory: Callable[[tools.IncidentView], object], cfg: InvestigationConfig = InvestigationConfig(),
        clock_factory: Callable[[tools.ToolContext], Callable[[], float]] | None = None,
        offset_s: int = 0) -> InvestigationRun:
    out = InvestigationRun()
    explainer = TemplateExplainer()
    for view in views_at_creation(stage6, list(candidates), offset_s):
        registry = Registry()
        ctx = tools.ToolContext(runner, db, run_id, world_start, view, registry, cfg)
        clock = clock_factory(ctx) if clock_factory else (lambda ctx=ctx: ctx.calls * SECONDS_PER_CALL)
        chooser = chooser_factory(view)
        out.events.append(Event(view.as_of, "investigation", incident_id=view.incident_id, command="start"))
        inv = investigate(view, ctx, chooser, clock, cfg)
        inv.steps.append({"phase": "audit", "tool_calls": ctx.audit})
        report = validate(explainer.explain(inv, registry), registry)
        status = STATUS.get(inv.stop_reason, "failed") if report.valid else "failed"
        out.events.append(Event(view.as_of, "investigation", incident_id=view.incident_id, command="finish",
                                reason=status, target_id=inv.investigation_id))
        out.investigations.append(inv)
        out.registries[inv.investigation_id] = registry
        out.reports[inv.investigation_id] = report
        out.views[inv.investigation_id] = view
    return out
