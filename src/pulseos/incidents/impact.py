"""Incident impact (ADR-041 D-6): one anchor per incident, so overlapping candidates are not counted twice.

Input: the incident's member candidates as known at the end (``CandidateInfo``), each member's episode end, a runner.
Output: a dict of labelled values — ``OBSERVED`` captured revenue and attempts are measured; ``ESTIMATED`` lost
successful payments and lost revenue carry an interval.
* the interval is the **anchor's episode** (ADR-042), not the incident span, which grows with its longest member;
* lost successful payments: the anchor (most specific approval / conversion member, latest analysis) Stage 4 estimate,
  extended from its analysis window to that interval;
* lost revenue per currency: Stage 3 lagged same-slot baseline (days d-8 … d-2, the most extreme dropped when ≥ 4)
  of ``revenue_collected_minor`` minus the observed revenue, in the anchor locus — or in its Stage 3 scope when the
  locus is a new cohort, which has no baseline (ADR-042); interval from the reference spread.
ClickHouse only through ``metrics.compute`` (INV-003). Never reads ground truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Iterable

from pulseos.incidents.config import IMPACT_METRICS
from pulseos.incidents.engine import CandidateInfo
from pulseos.metrics import compute

DAY = 86_400
REF_LAGS = (2, 3, 4, 5, 6, 7, 8)


def anchor(members: Iterable[CandidateInfo]) -> CandidateInfo | None:
    xs = [m for m in members if m.metric in IMPACT_METRICS and m.impact]
    if not xs:
        return None
    return max(xs, key=lambda m: (len(m.locus), m.window_end, m.candidate_id))


def lost_successes(a: CandidateInfo, start: int, end: int) -> dict | None:
    hours = max(1e-9, (a.window_end - a.start) / 3600)
    scale = max(1.0, (end - start) / 3600 / hours)
    imp = a.impact
    if not imp or imp.get("measure") != "lost_successes":
        return None
    lo, hi = imp["interval"]
    return {"value": round(imp["value"] * scale, 1), "interval": [round(lo * scale, 1), round(hi * scale, 1)],
            "epistemic": "ESTIMATED", "anchor": a.candidate_id}


def _hour(ts: int) -> int:
    return ts - ts % 3600


def revenue(runner, database: str, run_id: str, locus: frozenset, start: int, end: int, world_start: int) -> dict:
    s, e = _hour(start), _hour(end - 1) + 3600
    q0 = max(_hour(world_start), s - max(REF_LAGS) * DAY)
    pts = compute(runner, database, run_id, "revenue_collected_minor", 1, datetime.fromtimestamp(q0, timezone.utc),
                  datetime.fromtimestamp(e, timezone.utc), "1h", dict(locus), ("currency",), dense=False)
    by: dict[str, dict[int, int]] = {}
    for p in pts:
        by.setdefault(dict(p.dims)["currency"], {})[int(p.window_start.timestamp())] = p.numerator
    out = {}
    for cur, series in sorted(by.items()):
        observed = sum(v for t, v in series.items() if s <= t < e)
        refs = [sum(v for t, v in series.items() if s - k * DAY <= t < e - k * DAY)
                for k in REF_LAGS if s - k * DAY >= q0]
        if len(refs) < 2:
            continue
        if len(refs) >= 4:
            mid = sorted(refs)[len(refs) // 2]
            refs.remove(max(refs, key=lambda r: abs(r - mid)))
        base, sd = mean(refs), pstdev(refs)
        out[cur] = {"observed_minor": observed, "baseline_minor": round(base), "lost_minor": round(base - observed),
                    "interval": [round(base - 1.96 * sd - observed), round(base + 1.96 * sd - observed)]}
    return out


def target(members: list[CandidateInfo], episode_end: dict[str, int]) -> tuple[CandidateInfo | None, int, int, frozenset]:
    """(anchor, start, end, locus): the anchor's episode; a new cohort is measured on its Stage 3 scope."""
    a = anchor(members)
    ref = a or max(members, key=lambda m: (len(m.locus), m.candidate_id))
    locus = ref.scope if ref.change_type == "new_cohort" else ref.locus
    return a, ref.start, episode_end[ref.candidate_id], locus


def estimate(runner, database: str, run_id: str, members: Iterable[CandidateInfo], episode_end: dict[str, int],
             world_start: int) -> dict:
    members = list(members)
    a, start, end, locus = target(members, episode_end)
    rev = revenue(runner, database, run_id, locus, start, end, world_start) if end > start else {}
    return {
        "lost_successful_payments": lost_successes(a, start, end) if a else None,
        "lost_revenue_minor": {"epistemic": "ESTIMATED",
                               "per_currency": {c: {"value": v["lost_minor"], "interval": v["interval"]} for c, v in rev.items()}},
        "captured_revenue_minor": {"epistemic": "OBSERVED", "per_currency": {c: v["observed_minor"] for c, v in rev.items()}},
        "interval": [start, end], "locus": sorted([list(x) for x in locus]),
        "hours": round((end - start) / 3600, 2),
    }
