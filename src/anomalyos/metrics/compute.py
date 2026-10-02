"""Compute a metric series in ClickHouse and return a compact, dense list of points.

Why: detection and cohort analysis need numerator/denominator series per window and cohort;
aggregation must run in ClickHouse (AGENTS.md rule 1), never over raw rows in Python.

Contract: ``compute(runner, database, run_id, metric, version, start, end, grain, filters,
group_by) -> list[SeriesPoint]``.
* ``start`` is grain-aligned (UTC); ``end`` is the as-of bound: only events with
  ``occurred_at < end`` are read (no future leakage); the last window is ``is_complete=False``
  when it extends past ``end``.
* ``visibility="window_close"`` keeps only rows with ``ingested_at <= window end`` (what was known when the window
  closed; late events are invisible until delivered, ADR-034);
* every point carries numerator and denominator; ``value`` is ``None`` when the denominator is 0
  (never 0, never NaN).
* the result is dense: every window of every group seen in the data is present (windows with no
  rows carry numerator 0 and the metric's ``empty_denominator``), so detectors need no gap logic.
* ``filters`` and ``group_by`` accept only dimensions that are in the DATA_MODEL cohort list *and*
  allowed for the metric; anything else raises ``MetricError``. Identifiers are validated, all
  values are bound as ClickHouse query parameters.

Failure modes: invalid input (``MetricError``); ClickHouse errors propagate unchanged. No ground
truth is ever read (INV-015).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence

from anomalyos.metrics.registry import DIMENSION_TYPES, GRAINS, MetricDef, get_metric

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_RUN_ID = re.compile(r"^run_[0-9a-f]{16}$")
MAX_WINDOWS = 20_000  # bounds the dense series (28 days of 5-minute windows = 8,064)
MAX_FILTER_VALUES = 100


class MetricError(ValueError):
    """Invalid metric request (unknown metric/dimension/grain, misaligned window, ...)."""


class QueryRunner(Protocol):
    """Minimal port to ClickHouse: run a parameterised SELECT, return rows as tuples."""

    def rows(self, sql: str, params: Mapping[str, Any]) -> list[tuple]: ...


class ClickHouseRunner:
    """``QueryRunner`` over a ``clickhouse_connect`` client."""

    def __init__(self, client):
        self._client = client

    def rows(self, sql: str, params: Mapping[str, Any]) -> list[tuple]:
        return [tuple(r) for r in self._client.query(sql, parameters=dict(params)).result_rows]


@dataclass(frozen=True)
class SeriesPoint:
    window_start: datetime  # UTC
    dims: tuple[tuple[str, str], ...]  # (dimension, value), in group_by order
    numerator: int
    denominator: int
    value: float | None
    is_complete: bool


def _epoch(dt: datetime, name: str) -> int:
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise MetricError(f"{name} must be a timezone-aware UTC datetime")
    return int(dt.timestamp())


def _dim_param(dim: str, value: Any) -> Any:
    if DIMENSION_TYPES[dim] == "UInt8":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise MetricError(f"filter {dim!r} expects integers 0..255, got {value!r}")
        return value
    if not isinstance(value, str):
        raise MetricError(f"filter {dim!r} expects strings, got {value!r}")
    return value


VISIBILITY = ("all", "window_close")


def _build(m: MetricDef, database: str, grain: str, filters: Mapping[str, Any], group_by: Sequence[str],
           visibility: str = "all") -> tuple[str, dict[str, Any]]:
    length, unit, _ = GRAINS[grain]
    window_end = f"(toStartOfInterval(occurred_at, INTERVAL {length} {unit}) + INTERVAL {length} {unit})"
    params: dict[str, Any] = {}
    preds = [m.where, "run_id = {run_id:String}",
             "occurred_at >= toDateTime({start:UInt32}, 'UTC')", "occurred_at < toDateTime({end:UInt32}, 'UTC')"]
    if visibility == "window_close":
        # "first look" (ADR-034): only what had reached the store when the window closed
        preds.append(f"ingested_at <= {window_end}")
    for i, (dim, raw) in enumerate(sorted(filters.items())):
        values = [raw] if isinstance(raw, (str, int)) and not isinstance(raw, bool) else list(raw)
        if not values or len(values) > MAX_FILTER_VALUES:
            raise MetricError(f"filter {dim!r} needs 1..{MAX_FILTER_VALUES} values")
        pname = f"f{i}"
        params[pname] = [_dim_param(dim, v) for v in values]
        preds.append(f"`{dim}` IN {{{pname}:Array({DIMENSION_TYPES[dim]})}}")
    dims_sql = "".join(f", `{d}`" for d in group_by)
    table = f"{database}.events_norm"
    source = m.source.replace("{table}", table)
    sql = (
        f"SELECT toUnixTimestamp(toStartOfInterval(occurred_at, INTERVAL {length} {unit})) AS window_start{dims_sql}, "
        f"toInt64({m.numerator}) AS numerator, toInt64({m.denominator}) AS denominator "
        f"FROM {source} WHERE {' AND '.join(preds)} "
        f"GROUP BY window_start{dims_sql} ORDER BY window_start{dims_sql}"
    ).replace("{table}", table).replace("{window_end}", window_end)
    return sql, params


def compute(
    runner: QueryRunner, database: str, run_id: str, metric: str, version: int, start: datetime, end: datetime,
    grain: str, filters: Mapping[str, Any] | None = None, group_by: Sequence[str] = (), *, dense: bool = True,
    visibility: str = "all",
) -> list[SeriesPoint]:
    if not _IDENT.match(database):
        raise MetricError(f"invalid database name {database!r}")
    if not _RUN_ID.match(run_id):
        raise MetricError(f"invalid run_id {run_id!r}")
    try:
        m = get_metric(metric, version)
    except KeyError as exc:
        raise MetricError(str(exc)) from None
    if visibility not in VISIBILITY:
        raise MetricError(f"unknown visibility {visibility!r}; allowed: {VISIBILITY}")
    if grain not in GRAINS:
        raise MetricError(f"unknown grain {grain!r}; allowed: {sorted(GRAINS)}")
    seconds = GRAINS[grain][2]
    start_ts, end_ts = _epoch(start, "start"), _epoch(end, "end")
    if start_ts % seconds:
        raise MetricError(f"start must be aligned to the {grain} grain (UTC)")
    if end_ts <= start_ts:
        raise MetricError("end must be after start")
    n_windows = -(-(end_ts - start_ts) // seconds)
    if n_windows > MAX_WINDOWS:
        raise MetricError(f"{n_windows} windows exceed the limit of {MAX_WINDOWS}; use a coarser grain or a shorter range")
    filters = dict(filters or {})
    group_by = list(dict.fromkeys([*m.required_group_by, *group_by]))
    for kind, dims in (("filter", filters), ("group_by", group_by)):
        for d in dims:
            if d not in DIMENSION_TYPES:
                raise MetricError(f"{kind} dimension {d!r} is not a cohort dimension; allowed: {sorted(DIMENSION_TYPES)}")
            if d not in m.allowed_dims:
                raise MetricError(f"{kind} dimension {d!r} is not available for {m.name} v{m.version}; allowed: {sorted(m.allowed_dims)}")

    sql, params = _build(m, database, grain, filters, group_by, visibility)
    params.update(run_id=run_id, start=start_ts, end=end_ts)
    result = runner.rows(sql, params)

    n_dims = len(group_by)
    seen: dict[tuple[str, ...], dict[int, tuple[int, int]]] = {}
    for row in result:
        ts, dims, num, den = int(row[0]), tuple(str(x) for x in row[1:1 + n_dims]), int(row[1 + n_dims]), int(row[2 + n_dims])
        seen.setdefault(dims, {})[ts] = (num, den)
    if dense and not seen and not group_by:
        seen[()] = {}
    first = start_ts
    window_starts = [first + i * seconds for i in range(n_windows)]
    points: list[SeriesPoint] = []
    for dims in sorted(seen):
        by_ts = seen[dims]
        for ts in window_starts if dense else sorted(by_ts):
            num, den = by_ts.get(ts, (0, m.empty_denominator))
            points.append(SeriesPoint(
                window_start=datetime.fromtimestamp(ts, tz=timezone.utc), dims=tuple(zip(group_by, dims)),
                numerator=num, denominator=den, value=None if den == 0 else num / den, is_complete=ts + seconds <= end_ts))
    points.sort(key=lambda p: (p.window_start, p.dims))
    return points
