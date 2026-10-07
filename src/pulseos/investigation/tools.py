"""The ten investigation tools (ADR-049 D-3): typed, read-only, closed-set arguments, bounded output, one evidence
object each with a stable id and per-field epistemic labels.

Input: a ``ToolContext`` (runner, database, run, the incident as known at ``as_of``, the evidence registry) and the
tool's closed-set arguments. Output: an ``Evidence``. Reads go only through ``metrics.compute`` (via ``measure`` and
``cohorts.fetch``), ``incidents.impact``, ``pulseos.context`` and in-memory Stage 6 snapshots; no SQL text is built
from arguments (INV-002, INV-003). No tool writes anything (INV-007). Failure modes: ``ToolError`` for an argument
outside its closed set or a failed read; the loop records it and continues once, stops the second time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pulseos.cohorts.config import COMBINATIONS
from pulseos.cohorts.fetch import query_pooled
from pulseos.context import readers
from pulseos.incidents import impact as impact_mod
from pulseos.incidents.engine import CandidateInfo
from pulseos.investigation.config import InvestigationConfig
from pulseos.investigation.evidence import Evidence, Registry, evidence_id
from pulseos.investigation.measure import measure, outcome, version_of

H = 3600
LAGS = (2, 3, 4, 5, 6, 7, 8)


class ToolError(RuntimeError):
    """A tool could not run: an argument outside its closed set, or a failed read."""


@dataclass(frozen=True)
class IncidentView:
    """An incident as known at ``as_of`` (its creation): members and their as-of Stage 4 / 5 information."""
    incident_id: str
    as_of: int
    title: str
    started_at: int
    members: tuple[CandidateInfo, ...]
    anchor: CandidateInfo
    grain: str
    earlier_incidents: tuple[dict, ...] = ()  # snapshots of incidents created before as_of
    stage4_top: tuple[tuple[str, tuple], ...] = ()  # the anchor's Stage 4 top cohorts: (evidence id, dims), k <= 5


@dataclass
class ToolContext:
    runner: Any
    db: str
    run_id: str
    world_start: int
    view: IncidentView
    registry: Registry
    cfg: InvestigationConfig
    calls: int = 0
    audit: list[dict] = field(default_factory=list)


def _evidence(ctx: ToolContext, tool: str, args: tuple, payload: dict, labels: dict) -> Evidence:
    e = Evidence(evidence_id(tool, args, ctx.run_id, ctx.view.as_of, ctx.cfg.version), tool, args, payload, labels,
                 ctx.view.as_of)
    return ctx.registry.add(e)


def _window(ctx: ToolContext) -> tuple[int, int]:
    return ctx.view.anchor.start, ctx.view.as_of


def get_incident(ctx: ToolContext) -> Evidence:
    v = ctx.view
    payload = {"incident_id": v.incident_id, "title": v.title, "started_at": v.started_at, "detected_at": v.as_of,
               "members": len(v.members), "anchor_metric": v.anchor.metric, "direction": v.anchor.direction,
               "locus": sorted([list(x) for x in v.anchor.locus]), "change_type": v.anchor.change_type or "not_provided"}
    labels = {"title": "inferred", "started_at": "observed", "detected_at": "observed", "members": "observed",
              "anchor_metric": "observed", "direction": "observed", "locus": "inferred", "change_type": "inferred"}
    return _evidence(ctx, "get_incident", (), payload, labels)


def _measured(ctx, tool, args, metric, cohort, direction) -> Evidence:
    ws, we = _window(ctx)
    m = measure(ctx.runner, ctx.db, ctx.run_id, metric, cohort, ws, we, ctx.view.grain, ctx.world_start, ctx.cfg)
    if m is None:
        raise ToolError(f"{tool}: {metric} cannot be measured on {sorted(cohort)}")
    out = outcome(m.z, direction, ctx.cfg)
    payload = {"metric": metric, "cohort": [list(x) for x in m.cohort], "before": round(m.before, 6),
               "during": round(m.during, 6), "z": None if m.z is None else round(m.z, 3), "outcome": out}
    labels = {"metric": "observed", "cohort": "observed", "before": "observed", "during": "observed", "z": "observed",
              "outcome": "inferred"}
    return _evidence(ctx, tool, args, payload, labels)


def get_metric_history(ctx: ToolContext, metric: str, cohort: tuple, direction: str) -> Evidence:
    return _measured(ctx, "get_metric_history", (metric, cohort, direction), metric, dict(cohort), direction)


def compare_cohorts(ctx: ToolContext, metric: str, locus: tuple, sibling: tuple, direction: str) -> Evidence:
    """The sibling's outcome answers the check; the locus measurement is kept beside it for the report."""
    if set(dict(locus)) != set(dict(sibling)) or sum(1 for d, v in sibling if dict(locus).get(d) != v) != 1:
        raise ToolError("compare_cohorts: the cohorts must differ in exactly one dimension")
    e = _measured(ctx, "compare_cohorts", (metric, locus, sibling, direction), metric, dict(sibling), direction)
    return e


def get_related_metrics(ctx: ToolContext, metric: str, locus: tuple, direction: str) -> Evidence:
    from pulseos.investigation.measure import allowed
    from pulseos.metrics import get_metric
    dims = set(get_metric(metric, version_of(metric)).allowed_dims)
    cohort = {d: v for d, v in locus if d in dims}
    if not allowed(metric, cohort):
        raise ToolError(f"get_related_metrics: {metric} not measurable on the locus")
    return _measured(ctx, "get_related_metrics", (metric, locus, direction), metric, cohort, direction)


def breakdown_by_dimension(ctx: ToolContext, metric: str, dimension: tuple) -> Evidence:
    """Top cohorts of an approved combination extending the anchor scope (``COMBINATIONS``), by contribution."""
    if tuple(dimension) not in COMBINATIONS:
        raise ToolError(f"breakdown_by_dimension: {dimension} is not an approved combination")
    ws, we = _window(ctx)
    scope = dict(ctx.view.anchor.scope)
    kind = "count" if metric in ("attempt_volume", "refund_count") else "rate"
    rows = query_pooled(ctx.runner, ctx.db, ctx.run_id, metric, version_of(metric), scope, tuple(dimension), ws, we,
                        ctx.view.grain, LAGS, ctx.world_start, kind)
    items = sorted(rows.values(), key=lambda r: (-(r.n_d if kind == "rate" else r.k_d), r.dims))[:10]
    payload = {"metric": metric, "dimension": list(dimension), "cohorts": [
        {"dims": [list(x) for x in r.dims], "before": round(r.k_b / r.n_b, 6) if r.n_b else None,
         "during": round(r.k_d / r.n_d, 6) if r.n_d else None, "support": r.n_d} for r in items]}
    return _evidence(ctx, "breakdown_by_dimension", (metric, tuple(dimension)), payload, {"cohorts": "observed"})


def calculate_impact(ctx: ToolContext) -> Evidence:
    v = ctx.view
    imp = impact_mod.estimate(ctx.runner, ctx.db, ctx.run_id, list(v.members),
                              {m.candidate_id: v.as_of for m in v.members}, ctx.world_start)
    lost = imp.get("lost_successful_payments") or {}
    payload = {"lost_successful_payments": lost.get("value"), "interval": lost.get("interval"),
               "lost_revenue": "not_provided", "hours": imp.get("hours")}
    return _evidence(ctx, "calculate_impact", (), payload, {"lost_successful_payments": "estimated",
                                                            "interval": "estimated", "hours": "observed"})


def read_evidence(ctx: ToolContext, evidence_id_: str) -> Evidence:
    e = ctx.registry.get(evidence_id_)
    if e is None:
        raise ToolError("read_evidence: unknown evidence id")
    return e


def search_similar_incidents(ctx: ToolContext) -> Evidence:
    """≤ 5 incidents created before ``as_of`` sharing the anchor metric and a locus dimension."""
    a = ctx.view.anchor
    dims = {d for d, _ in a.locus}
    hits = []
    for inc in ctx.view.earlier_incidents:
        if inc.get("anchor_metric") == a.metric and dims & set(inc.get("locus_dims", ())):
            hits.append({"incident_id": inc["incident_id"], "status": inc["status"], "started_at": inc["started_at"]})
    payload = {"similar": hits[:5]}
    return _evidence(ctx, "search_similar_incidents", (), payload, {"similar": "observed"})


def _side_window(ctx: ToolContext, window: str) -> tuple[int, int]:
    s = ctx.view.started_at
    if window == "onset_2h":
        return s - 2 * H, min(s + 2 * H, ctx.view.as_of)
    if window == "start_6h":
        return s - 6 * H, ctx.view.as_of
    raise ToolError(f"unknown window {window!r}")


def get_deployments(ctx: ToolContext, service: str, window: str) -> Evidence:
    if service not in readers.SERVICES:
        raise ToolError("get_deployments: unknown service")
    start, end = _side_window(ctx, window)
    rows = readers.deployments(ctx.runner, ctx.db, ctx.run_id, (service,), start, end, ctx.view.as_of)
    payload = {"service": service, "window": window, "present": bool(rows), "ids": [d.id for d in rows[:10]],
               "deploys": [{"version": d.version, "minutes_before_start": (ctx.view.started_at - d.deployed_at) // 60}
                           for d in rows[:10]]}
    return _evidence(ctx, "get_deployments", (service, window), payload, {"present": "observed", "deploys": "observed"})


def check_psp_status(ctx: ToolContext, psp: str, window: str) -> Evidence:
    if psp not in readers.PSPS:
        raise ToolError("check_psp_status: unknown psp")
    start, end = _side_window(ctx, window)
    rows = readers.psp_status(ctx.runner, ctx.db, ctx.run_id, (psp,), start, end, ctx.view.as_of)
    payload = {"psp": psp, "window": window, "components": sorted({s.component for s in rows}),
               "ids": [s.id for s in rows[:10]],
               "entries": [{"component": s.component, "level": s.level, "resolved": s.resolved_at is not None}
                           for s in rows[:10]]}
    return _evidence(ctx, "check_psp_status", (psp, window), payload, {"components": "observed", "entries": "observed"})


REGISTRY: dict[str, Callable[..., Evidence]] = {
    "get_incident": get_incident, "get_metric_history": get_metric_history, "breakdown_by_dimension": breakdown_by_dimension,
    "compare_cohorts": compare_cohorts, "get_related_metrics": get_related_metrics, "calculate_impact": calculate_impact,
    "read_evidence": read_evidence, "search_similar_incidents": search_similar_incidents,
    "get_deployments": get_deployments, "check_psp_status": check_psp_status,
}


def call(ctx: ToolContext, name: str, *args) -> Evidence:
    """The only way the loop runs a tool: registry lookup, budget counting, an audit row."""
    if name not in REGISTRY:
        raise ToolError(f"unknown tool {name!r}")
    ctx.calls += 1
    try:
        e = REGISTRY[name](ctx, *args)
        ctx.audit.append({"tool": name, "args": args, "evidence_id": e.evidence_id, "ok": True})
        return e
    except ToolError as exc:
        ctx.audit.append({"tool": name, "args": args, "evidence_id": None, "ok": False, "error": str(exc)})
        raise
    except Exception as exc:  # a failed read is a tool failure, never a crash of the investigation
        ctx.audit.append({"tool": name, "args": args, "evidence_id": None, "ok": False, "error": type(exc).__name__})
        raise ToolError(f"{name} failed: {type(exc).__name__}") from exc
