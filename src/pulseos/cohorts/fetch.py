"""Pooled before / during cohort tables from ClickHouse, only through ``metrics.compute`` (INV-003, design C-3).

Input: runner, database, run id, metric, scope filters, the candidate window, grain, world start.
Output: ``CohortRow``s per combination. One query per combination over [window start - 8 days, window end) at the
candidate's grain, first-look visibility; pooling happens in Python on compact rows. Before = the same windows on
days d-8 ... d-2 (Stage 3 baseline rule; the most extreme reference day dropped when >= 4 remain).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Iterable, Sequence

from pulseos.cohorts.analysis import CohortRow
from pulseos.metrics import GRAINS, compute

DAY = 86_400


def _dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def pool(points: Iterable, scope: dict[str, str], ws: int, we: int, lags: Sequence[int], world_start: int,
         kind: str) -> dict[tuple, CohortRow]:
    """Group compact series points by cohort and pool them into before / during."""
    series: dict[tuple, dict[int, tuple[int, int]]] = defaultdict(dict)
    for p in points:
        dims = tuple((d, v) for d, v in p.dims if d not in scope)
        series[dims][int(p.window_start.timestamp())] = (p.numerator, p.denominator)
    out = {}
    for dims, by_ts in series.items():
        def total(lo: int, hi: int) -> tuple[int, int]:
            vals = [v for ts, v in by_ts.items() if lo <= ts < hi]
            return sum(n for n, _ in vals), sum(d for _, d in vals)
        k_d, n_d = total(ws, we)
        days = [total(ws - k * DAY, we - k * DAY) for k in lags if ws - k * DAY >= world_start]
        if len(days) >= 4:
            vals = [(n / d if d else 0.0) if kind == "rate" else float(n) for n, d in days]
            mid = median(vals)
            drop = max(range(len(days)), key=lambda i: (abs(vals[i] - mid), i))
            days = [x for i, x in enumerate(days) if i != drop]
        if kind == "count":
            mean_b = sum(n for n, _ in days) / len(days) if days else 0.0
            out[dims] = CohortRow(dims, mean_b, 1.0, float(k_d), 1.0, tuple((float(n), 1.0) for n, _ in days))
        else:
            out[dims] = CohortRow(dims, float(sum(n for n, _ in days)), float(sum(d for _, d in days)), float(k_d),
                                  float(n_d), tuple((float(n), float(d)) for n, d in days))
    return out


def query_pooled(runner, database: str, run_id: str, metric: str, version: int, scope: dict[str, str],
                 group_by: Sequence[str], ws: int, we: int, grain: str, lags: Sequence[int], world_start: int,
                 kind: str) -> dict[tuple, CohortRow]:
    seconds = GRAINS[grain][2]
    start = max(world_start, ws - max(lags) * DAY)
    start -= (start - world_start) % seconds
    points = compute(runner, database, run_id, metric, version, _dt(start), _dt(we), grain, scope, tuple(group_by),
                     dense=False, visibility="window_close")
    return pool(points, scope, ws, we, lags, world_start, kind)
