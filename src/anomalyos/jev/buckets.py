"""Versioned bucket edges for the JevState (ADR-039 D-1). Jev never sees a number: code buckets it here.

Changing an edge changes the state schema: bump ``STATE_SCHEMA_VERSION`` in ``jev.state``.
"""

from __future__ import annotations

METRIC_FAMILY = {
    "authorization_rate": "approval", "checkout_conversion_rate": "conversion", "renewal_success_rate": "renewal",
    "dunning_recovery_rate": "dunning", "refund_count": "refunds", "refund_rate": "refunds",
    "duplicate_charge_rate": "duplicates", "attempt_volume": "volume", "fraud_flag_rate": "fraud",
    "subscription_cancellation_rate": "cancellations", "late_arrival_share": "ingestion",
}
INTRADAY_GRAINS = {"5m", "15m", "1h"}
# jev_state_v2: impact per hour of the first-look window; edges = 25/50/75 % quantiles on DEV seeds 1-10
# (scripts/tune_stage5_dev.py, ADR-040). v1 edges (10 / 100 / 1000 events per episode) assumed whole episodes.
IMPACT_RATE_EDGES = (3.8, 19.0, 44.0)


def _edges(x: float, edges: tuple[tuple[float, str], ...], last: str) -> str:
    for edge, name in edges:
        if x < edge:
            return name
    return last


def relative_change(rel: float | None, direction: str) -> str:
    """Drops on the rate scale (none … collapse); rises, of rates or counts, on the rise scale (slight … extreme)."""
    if direction == "down":
        if rel is None:
            return "not_provided"
        return _edges(abs(rel), ((0.02, "none"), (0.05, "slight"), (0.15, "moderate"), (0.50, "severe")), "collapse")
    if rel is None:  # expected zero: any rise is unbounded
        return "extreme"
    return _edges(abs(rel), ((0.5, "slight"), (1.0, "moderate"), (3.0, "severe")), "extreme")


def strength(z: float | None) -> str:
    if z is None:
        return "not_provided"
    return _edges(abs(z), ((4.0, "weak"), (8.0, "moderate")), "strong")


def detection_rule(methods: tuple[str, ...]) -> str:
    m = set(methods)
    if {"z_persistence", "cusum"} <= m:
        return "both"
    if "cusum" in m:
        return "cusum"
    if "z_persistence" in m:
        return "persistence"
    return "not_provided"


def sample_size(n: float | None) -> str:
    if n is None:
        return "not_provided"
    return _edges(n, ((300, "small"), (3000, "medium")), "large")


def share(x: float | None) -> str:
    if x is None:
        return "not_provided"
    return _edges(x, ((0.3, "minor"), (0.6, "partial"), (0.9, "most")), "nearly_all")


def cohorts_moved(n: int | None) -> str:
    if n is None:
        return "not_provided"
    return "none" if n == 0 else "one" if n == 1 else "few" if n <= 5 else "many"


def impact_rate(value: float | None, hours: float) -> str:
    """Estimated lost successes (or excess events) per hour of the observed window."""
    if value is None or hours <= 0:
        return "not_provided"
    a, b, c = IMPACT_RATE_EDGES
    return _edges(max(0.0, value) / hours, ((a, "negligible"), (b, "small"), (c, "medium")), "large")


def concurrent(n: int) -> str:
    return "none" if n == 0 else "one" if n == 1 else "several"
