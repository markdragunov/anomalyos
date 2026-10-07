"""One measurement primitive for every metric check (ADR-049 D-3): a cohort's pooled before / during counts on the
Stage 3 lagged same-slot baseline (``cohorts.fetch.query_pooled`` -> ``metrics.compute``, first-look visibility) and
its stabilized z; plus the fixed outcome rule. ClickHouse only through ``metrics.compute`` (INV-003)."""

from __future__ import annotations

from dataclasses import dataclass

from pulseos.cohorts.analysis import z_count, z_rate
from pulseos.cohorts.config import CohortConfig
from pulseos.cohorts.fetch import query_pooled
from pulseos.detection.config import SERIES
from pulseos.investigation.config import InvestigationConfig
from pulseos.metrics import get_metric

COUNT_METRICS = ("attempt_volume", "refund_count")
VERSION = {s.metric: s.version for s in SERIES} | {"dunning_recovery_rate": 1, "refund_count": 1}
OUTCOMES = ("moved", "opposite", "unchanged", "ambiguous", "insufficient")


@dataclass(frozen=True)
class Measurement:
    metric: str
    cohort: tuple[tuple[str, str], ...]
    before: float  # rate (rates) or mean per reference day (counts)
    during: float
    support_before: float
    support_during: float
    z: float | None


def version_of(metric: str) -> int:
    return VERSION.get(metric, 1)


def allowed(metric: str, cohort: dict[str, str]) -> bool:
    return set(cohort) <= set(get_metric(metric, version_of(metric)).allowed_dims)


def measure(runner, db: str, run_id: str, metric: str, cohort: dict[str, str], ws: int, we: int, grain: str,
            world_start: int, cfg: InvestigationConfig = InvestigationConfig()) -> Measurement | None:
    if not allowed(metric, cohort) or we <= ws:
        return None
    kind = "count" if metric in COUNT_METRICS else "rate"
    rows = query_pooled(runner, db, run_id, metric, version_of(metric), dict(cohort), (), ws, we, grain,
                        CohortConfig().ref_lag_days, world_start, kind)
    r = rows.get(())
    if r is None:
        return None
    key = tuple(sorted(cohort.items()))
    if kind == "count":
        z = z_count(r.k_b, max(1, len(r.ref_days)), r.k_d) if (r.k_b + r.k_d) >= 5 else None
        return Measurement(metric, key, r.k_b, r.k_d, r.k_b, r.k_d, z)
    ok = r.n_b >= cfg.min_support and r.n_d >= cfg.min_support
    z = z_rate(r.k_b, r.n_b, r.k_d, r.n_d) if ok else None
    return Measurement(metric, key, r.k_b / r.n_b if r.n_b else 0.0, r.k_d / r.n_d if r.n_d else 0.0, r.n_b, r.n_d, z)


def outcome(z: float | None, direction: str, cfg: InvestigationConfig = InvestigationConfig()) -> str:
    """``moved`` |z| >= 2 in the expected direction, ``opposite`` against it, ``unchanged`` |z| < 1, else ``ambiguous``;
    no z (fewer than 30 attempts in a period) -> ``insufficient``."""
    if z is None:
        return "insufficient"
    signed = z if direction == "up" else -z
    if signed >= cfg.z_moved:
        return "moved"
    if signed <= -cfg.z_moved:
        return "opposite"
    if abs(z) < cfg.z_unchanged:
        return "unchanged"
    return "ambiguous"
