"""Versioned cohort configuration (ADR-037, design C-2). Changing anything here bumps COHORT_CONFIG_VERSION.

Approved combinations come from spec 05; a combination is used only when the candidate's metric allows all its
dimensions and it adds at least one dimension beyond the candidate's scope. Thresholds were fixed at Gate 0, before
any result was seen.
"""

from __future__ import annotations

from dataclasses import dataclass

COHORT_CONFIG_VERSION = 2  # 2: Gate 1 (ADR-038) concentration rule, canonical locus, new cohorts, sweep off

COMBINATIONS: tuple[tuple[str, ...], ...] = (
    ("psp",), ("customer_country",), ("payment_method_type",), ("card_brand",), ("platform",), ("app_version",),
    ("psp", "customer_country"), ("psp", "card_brand"), ("customer_country", "payment_method_type"),
    ("psp", "customer_country", "platform"),
)
SWEEP_COMBINATIONS: tuple[tuple[str, ...], ...] = (("psp", "customer_country"), ("customer_country", "payment_method_type"))

# Sibling metrics reported for the locus (design C-1 `related_metrics`, at most 2).
RELATED_METRICS: dict[str, tuple[tuple[str, int], ...]] = {
    "authorization_rate": (("checkout_conversion_rate", 2), ("attempt_volume", 1)),
    "checkout_conversion_rate": (("authorization_rate", 1), ("attempt_volume", 1)),
    "attempt_volume": (("authorization_rate", 1), ("checkout_conversion_rate", 2)),
    "refund_count": (("authorization_rate", 1), ("attempt_volume", 1)),
    "renewal_success_rate": (("dunning_recovery_rate", 1), ("authorization_rate", 1)),
    "dunning_recovery_rate": (("renewal_success_rate", 1), ("authorization_rate", 1)),
}


@dataclass(frozen=True)
class CohortConfig:
    min_support: int = 30  # attempts in each period, rates (ADR-026)
    min_count_events: int = 5  # events before + during, counts (Stage 3 min expected count)
    bh_q: float = 0.05  # Benjamini-Hochberg per candidate
    sweep_q: float = 0.01  # Benjamini-Hochberg per sweep day
    locus_coverage: float = 0.6  # share of the parent's change a locus must explain
    locus_lift: float = 1.5  # a sub-cohort replaces its parent only if its share of the change / share of traffic >= this
    rate_share_high: float = 0.7  # >= -> rate_change
    rate_share_low: float = 0.3  # <= -> mix_shift
    volume_only_z: float = 2.0  # count candidates: locus approval |z| below this -> volume_only
    control_z: float = 1.0  # controls: |z| below this
    top_k: int = 5
    max_controls: int = 3
    max_cohorts_per_combination: int = 200
    ref_lag_days: tuple[int, ...] = (2, 3, 4, 5, 6, 7, 8)  # Stage 3 baseline rule
    bundle_max_bytes: int = 8192
    sweep_enabled: bool = False  # Gate 1 (ADR-038): the daily sweep is report-only; Mode A does not use S12
