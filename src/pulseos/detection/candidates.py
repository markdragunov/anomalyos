"""Candidate object, statuses and the Mode A prefilter (spec 04, ADR-034).

Statuses: ``candidate`` (internal) -> ``promoted`` (handed to Stage 4/5) | ``suppressed`` (prefilter, with a reason);
``promoted`` -> ``recovered`` (recovery detector). No severity, no cause. ``anomaly_id`` is a stable hash of metric,
version, scope and first window, so a replay yields the same ids.

A candidate describes a whole episode (``score`` is the episode maximum, ``window_end`` / ``recovered_at`` are known
when it ends). ``as_of_view`` keeps only what was known at ``detected_at`` — the input of a first-look decision
(ADR-039 D-0); ``score_at_detection`` / ``methods_at_detection`` exist for it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace


def stable_hash(*parts: object, n: int = 16) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:n]


@dataclass(frozen=True)
class AnomalyCandidate:
    anomaly_id: str
    system: str  # "main" | "static"
    series_key: str
    metric: str
    metric_version: int
    grain: str
    scope: tuple[tuple[str, str], ...]
    direction: str  # direction of the change observed: "down" | "up"
    window_start: int  # epoch seconds, first flagged window
    window_end: int  # end of the last flagged window
    detected_at: int  # close of the window in which the rule first fired (no information after it was used)
    observed: float
    expected: float
    delta: float
    relative_delta: float | None
    score: float
    sample_size: int
    method: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    status: str = "promoted"
    status_reason: str = ""
    recovered_at: int | None = None
    parent_id: str | None = None
    score_at_detection: float | None = None  # z (or static score) of the window that opened the episode
    methods_at_detection: tuple[str, ...] = ()  # rules that fired in that window
    extra: dict = field(default_factory=dict, compare=False)


def as_of_view(c: AnomalyCandidate) -> AnomalyCandidate:
    """The candidate as known at ``detected_at``: first window only, no episode maximum, recovery or parent link."""
    if c.score_at_detection is None:
        raise ValueError(f"{c.anomaly_id}: no score_at_detection (candidate from an older detector)")
    return replace(c, window_end=c.detected_at, score=c.score_at_detection, method=c.methods_at_detection,
                   evidence_ids=c.evidence_ids[:1], status="promoted" if c.status == "recovered" else c.status,
                   recovered_at=None, parent_id=None)


def concurrent_at(c: AnomalyCandidate, others: list[AnomalyCandidate]) -> list[AnomalyCandidate]:
    """Other candidates already detected at ``c.detected_at`` and still open during ``[c.window_start, as_of)``."""
    as_of = c.detected_at
    return [o for o in others if o.anomaly_id != c.anomaly_id and o.detected_at <= as_of and o.window_start < as_of
            and (o.recovered_at is None or o.recovered_at > c.window_start)]  # both sides known at as_of


def evidence_id(metric: str, version: int, scope, grain: str, window_start: int) -> str:
    return "evd_" + stable_hash(metric, version, scope, grain, window_start)


def prefilter(cands: list[AnomalyCandidate], min_sample: int) -> list[AnomalyCandidate]:
    """Deterministic Mode A prefilter. Every decision carries a reason.

    * low volume: sample size below ``min_sample`` -> suppressed (``insufficient_data``);
    * parent link: a candidate on a sub-scope (psp, country, platform) that overlaps an open candidate of the
      same metric on the global scope is linked to it (``parent_id``), not dropped.
    Duplicates and adjacent windows are already merged per series and scope by the detector lifecycle.
    Not applicable yet (no data source): health checks, probes, heartbeats, maintenance windows."""
    out = []
    globals_ = [c for c in cands if not c.scope]
    for c in sorted(cands, key=lambda c: (c.detected_at, c.series_key, c.scope, c.system)):
        if c.sample_size < min_sample and c.metric != "attempt_volume":
            c = replace(c, status="suppressed", status_reason="insufficient_data")
        if c.scope:
            parent = next((g for g in globals_ if g.metric == c.metric and g.system == c.system
                           and g.window_start < c.window_end and c.window_start < g.window_end), None)
            if parent is not None:
                c = replace(c, parent_id=parent.anomaly_id)
        out.append(c)
    return out
