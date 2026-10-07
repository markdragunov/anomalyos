"""Parent locus for incident correlation (ADR-052 option A).

Why: a change on a scoped series (a country) can be part of a broader event located on another dimension (a PSP
outage). The candidate's own locus always keeps its scope, so Stage 6 could not link the two.
Input: a candidate (first-look or checkpoint view) with a non-global Stage 3 scope ``S``; runner, database, run, world
start, the cohort configuration. Output: the parent locus ``P`` — the Stage 4 locus of the same change analysed with a
global scope — or ``None``. ``P`` is kept only if it is a sub-cohort sharing no dimension with ``S`` and the cohort
``S ∪ P`` moves in the candidate's direction with |z| >= ``PARENT_Z`` on the same pooled periods.
Invariants: pure apart from pooled reads through ``metrics.compute`` (``cohorts.fetch``); no ground truth (INV-015);
the candidate's own Stage 4 analysis, bundle and evidence ids are untouched — only correlation reads ``P``.
"""

from __future__ import annotations

from typing import Any

from pulseos.cohorts.analysis import Dims, analyze, z_count, z_rate
from pulseos.cohorts.config import COHORT_CONFIG_VERSION, COMBINATIONS, CohortConfig
from pulseos.cohorts.fetch import query_pooled
from pulseos.metrics import get_metric

PARENT_Z = 2.0  # the Stage 7 "moved" threshold
COUNT_METRICS = ("attempt_volume", "refund_count")


def _z(row, kind: str, cfg: CohortConfig) -> float | None:
    if kind == "count":
        return z_count(row.k_b, max(1, len(row.ref_days)), row.k_d) if row.k_b + row.k_d >= cfg.min_count_events else None
    return z_rate(row.k_b, row.n_b, row.k_d, row.n_d) if min(row.n_b, row.n_d) >= cfg.min_support else None


def acceptable(locus, locus_is_scope: bool, label: str, scope: dict[str, str]) -> bool:
    """A parent candidate: a sub-cohort (not global, not a new cohort) sharing no dimension with the scope."""
    return bool(locus) and not locus_is_scope and label != "new_cohort" and not ({d for d, _ in locus} & set(scope))


def consistent(z: float | None, direction: str) -> bool:
    """The scope's own cohort ``S ∪ P`` moved in the candidate's direction by at least ``PARENT_Z``."""
    return z is not None and (z if direction == "up" else -z) >= PARENT_Z


def parent_locus(runner, database: str, run_id: str, cand: dict[str, Any], world_start: int,
                 cfg: CohortConfig = CohortConfig()) -> tuple[Dims, int] | None:
    """``(P, queries)`` or ``None``; ``cand`` is a candidate dict (``dataclasses.asdict`` of a view)."""
    scope = {d: v for d, v in cand["scope"]}
    if not scope:
        return None
    metric, version = cand["metric"], cand["metric_version"]
    kind = "count" if metric in COUNT_METRICS else "rate"
    allowed = set(get_metric(metric, version).allowed_dims)
    ws, we, grain = cand["window_start"], cand["window_end"], cand["grain"]
    tables, queries = {}, 0
    for combo in COMBINATIONS:
        if not set(combo) <= allowed:
            continue
        rows = query_pooled(runner, database, run_id, metric, version, {}, combo, ws, we, grain, cfg.ref_lag_days,
                            world_start, kind)
        queries += 1
        if rows:
            tables[combo] = sorted(rows.values(), key=lambda r: (-r.n_d if kind == "rate" else -r.k_d, r.dims))
    a = analyze(dict(cand, scope=[]), tables, cfg, COHORT_CONFIG_VERSION)
    if not acceptable(a.locus, a.locus_is_scope, a.label, scope):
        return None
    p = tuple(tuple(x) for x in a.locus)
    joint = dict(scope) | dict(p)
    if not set(joint) <= allowed:
        return None
    row = query_pooled(runner, database, run_id, metric, version, joint, (), ws, we, grain, cfg.ref_lag_days,
                       world_start, kind).get(())
    queries += 1
    z = _z(row, kind, cfg) if row is not None else None
    return (p, queries) if consistent(z, cand["direction"]) else None
