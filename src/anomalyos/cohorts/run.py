"""Analyze promoted detection candidates: tables -> analysis -> related metrics -> impact -> bundle (ADR-037).

Input: runner, database, run id, promoted Stage 3 candidates (sweep candidates only in reports), world start, config. Output: list of
``(CohortAnalysis, EvidenceBundle dict)``. Never reads ground truth (INV-015); ClickHouse only via ``metrics.compute``.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from anomalyos.cohorts.analysis import (CohortAnalysis, analyze, bundle, estimate_impact, evidence_id, locus_row,
                                        naive_locus, phi_from_reference_days, z_count, z_rate)
from anomalyos.cohorts.config import COHORT_CONFIG_VERSION, COMBINATIONS, RELATED_METRICS, CohortConfig
from anomalyos.cohorts.fetch import query_pooled
from anomalyos.metrics import get_metric

COUNT_METRICS = ("attempt_volume", "refund_count")


def _kind(metric: str) -> str:
    return "count" if metric in COUNT_METRICS else "rate"


def _related(runner, database, run_id, a: CohortAnalysis, cand: dict, world_start: int, cfg: CohortConfig) -> list[dict]:
    out = []
    loc = dict(a.locus or ())
    for metric, version in RELATED_METRICS.get(a.metric, ())[:2]:
        allowed = set(get_metric(metric, version).allowed_dims)
        if not set(loc) <= allowed:
            continue
        kind = _kind(metric)
        rows = query_pooled(runner, database, run_id, metric, version, loc, (), cand["window_start"], cand["window_end"],
                            cand["grain"], cfg.ref_lag_days, world_start, kind)
        a.queries += 1
        r = rows.get(())
        if r is None:
            continue
        if kind == "rate":
            if r.n_b < cfg.min_support or r.n_d < cfg.min_support:
                continue
            z = z_rate(r.k_b, r.n_b, r.k_d, r.n_d)
            item = {"metric": metric, "version": version, "rate_before": round(r.k_b / r.n_b, 6), "rate_during": round(r.k_d / r.n_d, 6)}
        else:
            z = z_count(r.k_b, max(1, len(r.ref_days)), r.k_d)
            item = {"metric": metric, "version": version, "mean_before": round(r.k_b, 2), "during": r.k_d}
        item.update(z=round(z, 3) if z is not None else None, epistemic="observed",
                    evidence_id=evidence_id(metric, version, tuple(sorted(loc.items())), a.window))
        out.append(item)
    return out


def analyze_one(runner, database: str, run_id: str, cand: dict, world_start: int, cfg: CohortConfig) -> tuple[CohortAnalysis, dict]:
    metric, version = cand["metric"], cand["metric_version"]
    kind = _kind(metric)
    allowed = set(get_metric(metric, version).allowed_dims)
    scope = {d: v for d, v in cand["scope"]}
    tables, queries = {}, 0
    for combo in COMBINATIONS:
        if not set(combo) <= allowed or set(combo) <= set(scope):
            continue
        rows = query_pooled(runner, database, run_id, metric, version, scope, combo, cand["window_start"],
                            cand["window_end"], cand["grain"], cfg.ref_lag_days, world_start, kind)
        queries += 1
        if rows:
            tables[combo] = sorted(rows.values(), key=lambda r: (-r.n_d if kind == "rate" else -r.k_d, r.dims))
    a = analyze(cand, tables, cfg, COHORT_CONFIG_VERSION)
    a.queries = queries
    if kind == "rate":
        a.naive_locus = naive_locus(tables, a.scope, a.direction, cfg)
    a.related_metrics = _related(runner, database, run_id, a, cand, world_start, cfg) if a.locus is not None else []
    if metric == "attempt_volume" and a.locus is not None:  # refund spikes do not move approval (ADR-038)
        approval = next((m for m in a.related_metrics if m["metric"] == "authorization_rate"), None)
        if approval is not None and approval["z"] is not None and abs(approval["z"]) < cfg.volume_only_z:
            a.label = "volume_only"  # demand moved, approval did not (design C-4)
    combo_phi = phi_from_reference_days(tables.get(a.label_combination or (), []) or next(iter(tables.values()), []),
                                        kind, cfg.min_support) if tables else 1.0
    a.impact = estimate_impact(a, locus_row(a, tables), combo_phi)
    return a, bundle(a, cand, cfg)


def _as_dict(c: Any) -> dict:
    return asdict(c) if is_dataclass(c) else dict(c)


def link_related(results: list[tuple[CohortAnalysis, dict]]) -> None:
    """Link (not merge) candidates of the same metric whose windows overlap and whose loci are nested (design C-9)."""
    for i, (a, b) in enumerate(results):
        la = set(a.locus or a.scope)
        for j, (o, _) in enumerate(results):
            if i == j or o.metric != a.metric:
                continue
            if not (a.window[0] < o.window[1] and o.window[0] < a.window[1]):
                continue
            lo = set(o.locus or o.scope)
            if la <= lo or lo <= la:
                a.related_candidates.append(o.candidate_id)
        b["related_candidates"] = sorted(a.related_candidates)[:5]


def analyze_candidates(runner, database: str, run_id: str, candidates: Iterable[Any], world_start: int,
                       cfg: CohortConfig = CohortConfig()) -> list[tuple[CohortAnalysis, dict]]:
    results = []
    for c in candidates:
        cand = _as_dict(c)
        if cand["status"] == "suppressed":
            continue
        results.append(analyze_one(runner, database, run_id, cand, world_start, cfg))
    link_related(results)
    return results
