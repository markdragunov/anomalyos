"""Incident engine (ADR-041 D-0 … D-4): a pure fold over a time-ordered event stream.

Input: ``Event``s — candidate detected / checkpoint (each with its as-of Stage 4 locus and Stage 5 decision),
candidate recovered, recovery check, human action, impact estimated. Output: append-only rows for
``incident_events`` (one per change, with actor and reason), ``incident_links`` and ``digest_items``, plus the final
incident snapshots. Same events and versions => same rows. No I/O, no clock, no ground truth.
Failure modes: an illegal human command raises ``lifecycle.TransitionError`` (the command interface rejects it).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from pulseos.incidents import correlate, lifecycle
from pulseos.incidents.config import IncidentConfig
from pulseos.jev.buckets import METRIC_FAMILY

KIND_ORDER = {"detected": 0, "checkpoint": 1, "recovered": 2, "recovery_check": 3, "human": 4, "impact": 5}


def _h(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class CandidateInfo:
    """A candidate as known at the event time, with its latest Stage 4 locus and Stage 5 decision."""
    candidate_id: str
    metric: str
    direction: str
    start: int
    locus: frozenset  # (dimension, value) pairs; empty = global
    route: str  # IGNORE | DIGEST | INCIDENT
    decision_id: str
    route_source: str
    policy_version: str
    verified: bool = False
    incident_p: float | None = None
    severity_level: str = ""
    categories: tuple[str, ...] = ()  # top causes, best first
    evidence_ids: tuple[str, ...] = ()
    impact: dict | None = None  # Stage 4 estimate on the as-of window
    window_end: int = 0  # end of the as-of window the analysis used


@dataclass(frozen=True)
class Event:
    time: int
    kind: str
    candidate_id: str = ""
    info: CandidateInfo | None = None
    actor: str = "system"
    command: str = ""
    incident_id: str = ""
    target_id: str = ""
    reason: str = ""
    impact: dict | None = None

    @property
    def key(self) -> tuple:
        return (self.time, KIND_ORDER[self.kind], self.candidate_id or self.incident_id)


@dataclass
class _Group:
    gid: str
    kind: str  # "incident" | "digest"
    created_at: int
    members: dict[str, correlate.Member] = field(default_factory=dict)
    starts: dict[str, int] = field(default_factory=dict)
    status: str = "DETECTED"
    promoted_to: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    seq: int = 0


@dataclass
class EngineResult:
    incident_events: list[dict]
    links: list[dict]
    digest_items: list[dict]
    incidents: dict[str, dict]
    digest_groups: dict[str, list[str]]


class Engine:
    def __init__(self, cfg: IncidentConfig = IncidentConfig(), jev_evaluated: bool = False) -> None:
        self.cfg, self.jev_evaluated = cfg, jev_evaluated
        self.groups: dict[str, _Group] = {}
        self.home: dict[str, str] = {}  # candidate -> group id
        self.info: dict[str, CandidateInfo] = {}
        self.recovered: dict[str, int] = {}
        self.events: list[dict] = []
        self.links: list[dict] = []
        self.digest: list[dict] = []

    # ------------------------------------------------------------------ helpers
    def _interval(self, g: _Group, t: int) -> tuple[int, int]:
        start = min(g.starts.values())
        open_ = any(self.recovered.get(c) is None or self.recovered[c] > t for c in g.members)
        return start, (t if open_ else max(self.recovered[c] for c in g.members))

    def _open(self, kind: str, t: int) -> list[correlate.OpenGroup]:
        out = []
        for g in self.groups.values():
            if g.kind != kind or g.promoted_to or (kind == "incident" and g.status in lifecycle.TERMINAL):
                continue
            s, e = self._interval(g, t)
            out.append(correlate.OpenGroup(g.gid, (g.created_at, g.gid), s, e, tuple(g.members.values())))
        return out

    def _member(self, info: CandidateInfo) -> correlate.Member:
        return correlate.Member(info.candidate_id, info.metric, info.direction, info.locus)

    def _link(self, g: _Group, info: CandidateInfo, t: int, kind: str, evidence: dict) -> None:
        g.members[info.candidate_id] = self._member(info)
        g.starts[info.candidate_id] = info.start
        self.home[info.candidate_id] = g.gid
        if g.kind == "incident":
            self.links.append({"incident_id": g.gid, "candidate_id": info.candidate_id, "linked_at": t, "kind": kind,
                               "evidence_json": json.dumps(evidence, sort_keys=True)})
        else:
            self.digest.append({"candidate_id": info.candidate_id, "digest_group_id": g.gid, "time": t,
                                "decision_id": info.decision_id, "promoted_to": None})

    def _refresh(self, g: _Group, t: int) -> None:
        infos = [self.info[c] for c in g.members]
        latest = max((i for i in infos if i.verified), key=lambda i: (i.window_end, i.candidate_id), default=None)
        anchor = max(infos, key=lambda i: (len(i.locus), -i.start, i.candidate_id))
        first = self.info[min(g.members, key=lambda c: (g.starts[c], c))]
        loc = ", ".join(f"{d}={v}" for d, v in sorted(anchor.locus)) or "all traffic"
        g.fields.update({
            "title": f"{METRIC_FAMILY.get(first.metric, first.metric)} {first.direction} in {loc}",
            "started_at": min(g.starts.values()), "detected_at": g.created_at, "last_updated_at": t,
            "affected_dimensions": sorted([list(x) for x in anchor.locus]),
            "category": latest.categories[0] if latest and latest.categories else None,
            "hypotheses": list(latest.categories[:3]) if latest else [],
            "severity": latest.severity_level if latest else None,
            "confidence": latest.incident_p if latest else None,
            "jev_status": "evaluated" if self.jev_evaluated else "not_evaluated",
            "epistemic": {"started_at": "OBSERVED", "affected_dimensions": "INFERRED", "category": "INFERRED",
                          "severity": "INFERRED", "confidence": "INFERRED", "estimated_impact": "ESTIMATED"},
            "linked_anomalies": sorted(g.members),
            "evidence_ids": sorted({e for i in infos for e in i.evidence_ids} | {i.decision_id for i in infos}),
            "investigation_status": "not_started",
            "route_source": first.route_source, "policy_version": first.policy_version,
        })

    def _row(self, g: _Group, t: int, actor: str, reason: str) -> None:
        self._refresh(g, t)
        g.seq += 1
        self.events.append({"incident_id": g.gid, "seq": g.seq, "event_time": t, "status": g.status, "actor": actor,
                            "reason": reason, "snapshot_json": json.dumps(g.fields, sort_keys=True)})

    def _transition(self, g: _Group, target: str, actor: str, reason: str, t: int) -> None:
        lifecycle.check(g.status, target, actor)
        g.status = target
        self._row(g, t, actor, reason)

    def _new_incident(self, seed: CandidateInfo, t: int) -> _Group:
        g = _Group("inc_" + _h(seed.candidate_id), "incident", t)
        self.groups[g.gid] = g
        return g

    def _promote(self, d: _Group, info: CandidateInfo | None, t: int, evidence: dict) -> _Group:
        first = self.info[min(d.members, key=lambda c: (d.starts[c], c))]
        g = self._new_incident(first, t)
        for cid in sorted(d.members, key=lambda c: (d.starts[c], c)):
            self._link(g, self.info[cid], t, "promote", {"digest_group": d.gid})
            self.digest.append({"candidate_id": cid, "digest_group_id": d.gid, "time": t,
                                "decision_id": self.info[cid].decision_id, "promoted_to": g.gid})
        d.promoted_to = g.gid
        if info is not None and info.candidate_id not in g.members:
            self._link(g, info, t, "join", evidence)
        self._row(g, t, "policy", "digest_promoted")
        return g

    # ------------------------------------------------------------------ events
    def _arrive(self, info: CandidateInfo, t: int) -> None:
        cid = info.candidate_id
        home = self.groups.get(self.home.get(cid, ""))
        if home is not None:  # a checkpoint refines the member's locus (Gate 1 fix, owner OK)
            home.members[cid] = self._member(info)
        if home is not None and home.kind == "incident":
            if info.verified:
                self._row(home, t, "policy", "checkpoint_update")
            return
        if home is not None:  # in a digest group
            if info.route == "INCIDENT":
                self._promote(home, None, t, {})
            return
        if info.route == "IGNORE":
            return
        member = self._member(info)
        m = correlate.decide(member, info.start, t, self._open("incident", t), self.cfg)
        if m.target is not None:
            g = self.groups[m.target]
            self._link(g, info, t, "join" if info.route == "INCIDENT" else "support", m.evidence)
            if g.status == "RECOVERING":
                self._transition(g, "INVESTIGATING", "system", "signal_returned", t)
            else:
                self._row(g, t, "policy", "candidate_linked")
            return
        dm = correlate.decide(member, info.start, t, self._open("digest", t), self.cfg)
        if info.route == "INCIDENT":
            if dm.target is not None:
                self._promote(self.groups[dm.target], info, t, dm.evidence)
                return
            g = self._new_incident(info, t)
            self._link(g, info, t, "create", m.evidence)
            for rid in m.related:  # recorded on the new incident; the related incident is named in the evidence
                self.links.append({"incident_id": g.gid, "candidate_id": cid, "linked_at": t, "kind": "related_to",
                                   "evidence_json": json.dumps(dict(m.evidence, related_incident=rid), sort_keys=True)})
            self._row(g, t, "policy", "incident_created")
            return
        if dm.target is not None:  # DIGEST joining a digest group
            self._link(self.groups[dm.target], info, t, "digest", dm.evidence)
            return
        d = _Group("dg_" + _h(cid), "digest", t)
        self.groups[d.gid] = d
        self._link(d, info, t, "digest", {})

    def _recovery_check(self, cid: str, t: int) -> None:
        g = self.groups.get(self.home.get(cid, ""))
        if g is None or g.kind != "incident" or g.status in ("RECOVERING",) + lifecycle.TERMINAL:
            return
        rec = [self.recovered.get(c) for c in g.members]
        if all(r is not None and r <= t for r in rec) and max(rec) + self.cfg.hysteresis_s <= t:
            self._transition(g, "RECOVERING", "system", "signals_recovered", t)

    def _human(self, ev: Event) -> None:
        g = self.groups[ev.incident_id]
        target = {"acknowledge": "ACKNOWLEDGED", "escalate": "ESCALATED", "resolve": "RESOLVED",
                  "dismiss": "DISMISSED"}.get(ev.command)
        if ev.command == "merge":
            dst = self.groups[ev.target_id]
            lifecycle.check(g.status, "DISMISSED", ev.actor)
            for cid in sorted(g.members):
                self._link(dst, self.info[cid], ev.time, "merge", {"from": g.gid, "by": ev.actor})
            self._row(dst, ev.time, ev.actor, f"merged from {g.gid}: {ev.reason}")
            self._transition(g, "DISMISSED", ev.actor, f"merged into {dst.gid}: {ev.reason}", ev.time)
            return
        if target is None:
            raise lifecycle.TransitionError(f"unknown command {ev.command!r}")
        if not ev.reason:
            raise lifecycle.TransitionError("a human command needs a reason")
        self._transition(g, target, ev.actor, ev.reason, ev.time)

    def apply(self, ev: Event) -> None:
        if ev.kind in ("detected", "checkpoint"):
            self.info[ev.info.candidate_id] = ev.info
            self._arrive(ev.info, ev.time)
        elif ev.kind == "recovered":
            self.recovered[ev.candidate_id] = ev.time
        elif ev.kind == "recovery_check":
            self._recovery_check(ev.candidate_id, ev.time)
        elif ev.kind == "human":
            self._human(ev)
        elif ev.kind == "impact":
            g = self.groups[ev.incident_id]
            g.fields["estimated_impact"] = ev.impact
            self._row(g, ev.time, "system", "impact_estimated")
        else:
            raise ValueError(f"unknown event kind {ev.kind!r}")

    def run(self, events: Iterable[Event]) -> EngineResult:
        for ev in sorted(events, key=lambda e: e.key):
            self.apply(ev)
        return self.result()

    def result(self) -> EngineResult:
        incidents = {g.gid: dict(g.fields, status=g.status, incident_id=g.gid)
                     for g in self.groups.values() if g.kind == "incident"}
        digests = {g.gid: sorted(g.members) for g in self.groups.values() if g.kind == "digest"}
        return EngineResult(list(self.events), list(self.links), list(self.digest), incidents, digests)
