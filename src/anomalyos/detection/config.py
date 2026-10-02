"""Monitored series and detector parameters (ADR-034). Parameters are tuned on DEV_SEEDS only.

Input: none (data). Output: ``SERIES`` (what is monitored, at which grain, in which direction) and
``DetectorConfig`` (thresholds). Invariants: every series names a registered metric version; scopes use
cohort dimensions only; grains follow ADR-026 (global 15 min / 1 h, PSP 1 h, slow drift daily).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeriesSpec:
    key: str
    metric: str
    version: int
    grain: str  # "15m" | "1h" | "1d"
    group_by: tuple[str, ...]
    direction: str  # "down" | "up" | "both"
    kind: str  # "rate" | "count"
    cusum: bool = False  # cumulative detector for slow drift (daily series)


SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec("S1", "authorization_rate", 1, "15m", (), "down", "rate"),
    SeriesSpec("S2", "authorization_rate", 1, "1h", ("psp",), "down", "rate"),
    SeriesSpec("S3", "authorization_rate", 1, "1d", ("customer_country",), "down", "rate", cusum=True),
    SeriesSpec("S3h", "authorization_rate", 1, "1h", ("customer_country",), "down", "rate"),  # Gate 1: short country outages
    SeriesSpec("S4a", "checkout_conversion_rate", 2, "1h", (), "down", "rate"),
    SeriesSpec("S4b", "checkout_conversion_rate", 2, "1h", ("platform",), "down", "rate"),
    SeriesSpec("S5", "renewal_success_rate", 1, "1d", ("psp",), "down", "rate", cusum=True),
    SeriesSpec("S5b", "dunning_recovery_rate", 1, "1d", ("psp",), "down", "rate", cusum=True),  # Gate 1: dunning failures
    SeriesSpec("S6", "refund_rate", 1, "1d", ("customer_country",), "up", "rate", cusum=True),
    SeriesSpec("S7", "duplicate_charge_rate", 1, "1h", (), "up", "rate"),
    SeriesSpec("S8", "attempt_volume", 1, "1h", ("psp",), "both", "count"),
    SeriesSpec("S9", "fraud_flag_rate", 1, "1h", (), "up", "rate"),
    SeriesSpec("S10", "subscription_cancellation_rate", 2, "1d", ("customer_country",), "up", "rate", cusum=True),  # Gate 1: v2
    SeriesSpec("S11", "late_arrival_share", 1, "1h", (), "up", "rate"),
)


@dataclass(frozen=True)
class DetectorConfig:
    # baseline (D-2): same slot, days d-8 .. d-2, the most extreme reference dropped when >= 4 remain
    ref_lag_days: tuple[int, ...] = (2, 3, 4, 5, 6, 7, 8)
    min_ref_windows: int = 2
    trim_when_at_least: int = 4
    warmup_days: int = 3
    # overdispersion (D-3): phi from the robust spread of recent unit-variance z, floored at 1
    phi_history: int = 168
    phi_min_history: int = 6
    phi_default: float = 2.0
    # gates and rules (D-3)
    min_sample: int = 30
    min_expected_count: float = 5.0
    min_excess_events: int = 3
    z_single: float = 4.0
    z_persist: float = 2.5
    persist_k: int = 2
    persist_n: int = 3
    cusum_k: float = 0.5
    cusum_h: float = 4.0
    recovery_windows: int = 2
    recovery_z: float = 1.0
    # static-threshold comparison system (spec 10): reference = median of the warm-up windows
    static_rate_drop: float = 0.15  # down: below (1 - x) * reference
    static_rate_rise: float = 2.0  # up: above x * reference
    static_count_factor: float = 2.0  # counts: outside [ref / x, ref * x]
