"""Build the incident engine's event stream from Stages 3-5 and run it (ADR-041 D-0, D-5, D-6).

Input: a query runner, database, run id, Stage 3 candidates, world bounds, a ``JevClient``, the pinned model, the route
source. Output: ``PipelineResult`` — engine rows, the decisions taken (first look and checkpoints), the events.
For every decided candidate: a ``detected`` event with the first-look decision (``policy.mode_a``); up to
``max_checkpoints`` ``checkpoint`` events (``detected_at`` + 6 h / 24 h while open, and ``recovered_at``), each with
Stage 4 on ``[window_start, t)`` and a new Stage 5 decision; a ``recovered`` event and a ``recovery_check`` after the
hysteresis. Route sources: ``policy`` (the decision's route), ``baseline`` (``baseline_v2`` on the same state — the
control), ``all`` (every decided candidate treated as INCIDENT, for route-agnostic evaluation).
A checkpoint keeps the first-look ``score_at_detection``: the episode's running maximum is not stored per window.
Nothing reads ground truth; ClickHouse only through Stage 4 and ``metrics.compute``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import Iterable

from pulseos.cohorts import CohortConfig, analyze_candidates
from pulseos.cohorts.parent import parent_locus
from pulseos.detection.candidates import AnomalyCandidate, as_of_view, concurrent_at
from pulseos.incidents import impact as impact_mod
from pulseos.incidents.config import IncidentConfig
from pulseos.incidents.engine import CandidateInfo, Engine, EngineResult, Event
from pulseos.jev.client import DecisionContext, JevClient
from pulseos.jev.state import JevState
from pulseos.policy import baseline, mode_a
from pulseos.policy.config import PolicyConfig
from pulseos.policy.decide import DecisionRecord, decide

ROUTE_SOURCES = ("policy", "baseline", "all")


@dataclass
class PipelineResult:
    engine: EngineResult
    decisions: list[DecisionRecord]
    events: list[Event]
    infos: dict[str, CandidateInfo]
    bundles: dict = None  # first-look Stage 4 bundles by candidate id (Stage 7 evaluation compares narrowing with them)


def _route(record: DecisionRecord, source: str) -> str:
    if source == "all":
        return "INCIDENT"
    if source == "policy":
        return record.route
    if record.state_json == "{}":
        return "DIGEST"
    s = json.loads(record.state_json)
    return baseline.route(JevState(**{k: tuple(v) if isinstance(v, list) else v for k, v in s.items()}))[0]


def _info(c: AnomalyCandidate, view: AnomalyCandidate, analysis, bundle, record: DecisionRecord, source: str,
          parent=None) -> CandidateInfo:
    locus = analysis.locus if analysis is not None and analysis.locus is not None else view.scope
    answers = json.loads(record.answers_json) if record.verified else {}
    cats = answers.get("category", {}).get("probs", {})
    return CandidateInfo(
        candidate_id=c.anomaly_id, metric=c.metric, direction=c.direction, start=c.window_start,
        locus=frozenset(tuple(x) for x in locus), route=_route(record, source), decision_id=record.decision_id,
        route_source={"policy": record.policy_version, "baseline": "baseline_v2", "all": "all_candidates"}[source],
        policy_version=record.policy_version, verified=record.verified, incident_p=record.incident_p,
        severity_level=record.severity_level, categories=tuple(sorted(cats, key=lambda k: (-cats[k], k))[:3]),
        evidence_ids=tuple(record.evidence_ids), impact=(bundle or {}).get("impact"), window_end=view.window_end,
        scope=frozenset(tuple(x) for x in view.scope), change_type=analysis.label if analysis is not None else "",
        parent_locus=frozenset(parent[0]) if parent else frozenset())


def checkpoint_times(c: AnomalyCandidate, world_end: int, cfg: IncidentConfig) -> list[int]:
    end = c.recovered_at if c.recovered_at is not None else world_end
    ts = [c.detected_at + s for s in cfg.checkpoints_s if c.detected_at + s < end]
    if c.recovered_at is not None:
        ts.append(c.recovered_at)
    return sorted(set(ts))[: cfg.max_checkpoints]


def run(runner, database: str, run_id: str, candidates: Iterable[AnomalyCandidate], world_start: int, world_end: int,
        client: JevClient, pinned_model: str, source: str = "policy", cfg: IncidentConfig = IncidentConfig(),
        policy_cfg: PolicyConfig = PolicyConfig(), cohort_cfg: CohortConfig = CohortConfig(),
        estimate_impact: bool = True, parent_loci: bool | None = None) -> PipelineResult:
    """``parent_loci``: compute ADR-052 parent loci (default: when ``cfg.parent_locus``); evaluation forces it on to
    refold one event stream under every option."""
    if source not in ROUTE_SOURCES:
        raise ValueError(f"unknown route source {source!r}")
    with_parent = cfg.parent_locus if parent_loci is None else parent_loci

    def parent_of(view: AnomalyCandidate):
        return parent_locus(runner, database, run_id, asdict(view), world_start, cohort_cfg) if with_parent else None

    full = mode_a.decidable(candidates)
    first = mode_a.run(runner, database, run_id, full, world_start, client, pinned_model, policy_cfg, cohort_cfg)
    by_id = {c.anomaly_id: c for c in full}
    events, decisions, infos = [], [], {}
    for d in first:
        c = by_id[d.record.candidate_id]
        info = _info(c, d.candidate, d.analysis, d.bundle, d.record, source, parent_of(d.candidate))
        infos[c.anomaly_id] = info
        decisions.append(d.record)
        events.append(Event(c.detected_at, "detected", c.anomaly_id, info=info))
        if c.recovered_at is not None:
            events.append(Event(c.recovered_at, "recovered", c.anomaly_id))
            events.append(Event(c.recovered_at + cfg.hysteresis_s, "recovery_check", c.anomaly_id))
    # checkpoints: Stage 4 on the as-of window [window_start, t), then a new decision at t
    views = []
    for c in full:
        for t in checkpoint_times(c, world_end, cfg):
            views.append((c, t, replace(as_of_view(c), window_end=t)))
    stage4 = {}
    if views:
        for (c, t, v), (a, b) in zip(views, analyze_candidates(runner, database, run_id, [v for _, _, v in views],
                                                               world_start, cohort_cfg)):
            stage4[(c.anomaly_id, t)] = (a, b)
    for c, t, v in views:
        a, b = stage4.get((c.anomaly_id, t), (None, None))
        ctx = DecisionContext(as_of=t, evaluated_at=t)
        record = decide(v, b, concurrent_at(replace(c, detected_at=t), full), client, ctx, pinned_model, policy_cfg)
        info = _info(c, v, a, b, record, source, parent_of(v))
        infos[c.anomaly_id] = info
        decisions.append(record)
        events.append(Event(t, "checkpoint", c.anomaly_id, info=info))
    engine = Engine(cfg)
    result = engine.run(events)
    if estimate_impact:
        for iid, inc in sorted(result.incidents.items()):
            members = [engine.info[cid] for cid in inc["linked_anomalies"]]
            episode_end = {c.anomaly_id: min(c.recovered_at or world_end, world_end) for c in full
                           if c.anomaly_id in inc["linked_anomalies"]}
            imp = impact_mod.estimate(runner, database, run_id, members, episode_end, world_start)
            engine.apply(Event(inc["last_updated_at"], "impact", incident_id=iid, impact=imp))
        result = engine.result()
    return PipelineResult(result, decisions, events, infos, {d.record.candidate_id: d.bundle for d in first})
