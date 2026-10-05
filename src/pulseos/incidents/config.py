"""Versioned incident-engine configuration (ADR-041). ``gap_s`` and ``hysteresis_s`` are tuned at Gate 1 on DEV seeds
1-10 only; every change bumps the version."""

from __future__ import annotations

from dataclasses import dataclass

HOUR = 3600

# Approved dimension chains (D-2): each pair (coarser, finer) of dimension sets; nesting also needs equal values.
CHAIN_EDGES: tuple[tuple[frozenset[str], frozenset[str]], ...] = tuple(
    (frozenset(a), frozenset(b)) for a, b in (
        (("psp",), ("psp", "customer_country")),
        (("psp", "customer_country"), ("psp", "customer_country", "platform")),
        (("psp",), ("psp", "card_brand")),
        (("customer_country",), ("psp", "customer_country")),
        (("customer_country",), ("customer_country", "payment_method_type")),
        (("platform",), ("platform", "app_version")),
    ))

# Metric groups (D-2), keyed by (metric, direction); None = either direction. "ingestion" is compatible with all.
METRIC_GROUPS: dict[tuple[str, str | None], frozenset[str]] = {
    ("authorization_rate", None): frozenset({"payments", "fraud"}),
    ("checkout_conversion_rate", None): frozenset({"payments"}),
    ("attempt_volume", "down"): frozenset({"payments"}),
    ("attempt_volume", "up"): frozenset({"fraud"}),
    ("fraud_flag_rate", None): frozenset({"fraud"}),
    ("renewal_success_rate", None): frozenset({"subscriptions"}),
    ("dunning_recovery_rate", None): frozenset({"subscriptions"}),
    ("refund_count", None): frozenset({"refunds"}),
    ("refund_rate", None): frozenset({"refunds"}),
    ("duplicate_charge_rate", None): frozenset({"refunds"}),
    ("subscription_cancellation_rate", None): frozenset({"cancellations"}),
    ("late_arrival_share", None): frozenset({"ingestion"}),
}
IMPACT_METRICS = ("authorization_rate", "checkout_conversion_rate")


@dataclass(frozen=True)
class IncidentConfig:
    version: str = "incidents_v1"
    gap_s: int = 1 * HOUR  # D-2 rule 1 (tuned over 0, 1, 3, 6 h)
    hysteresis_s: int = 1 * HOUR  # D-4 quiet time before RECOVERING (tuned over 0, 1, 3 h)
    checkpoints_s: tuple[int, ...] = (6 * HOUR, 24 * HOUR)  # D-5, after detected_at, while the episode is open
    max_checkpoints: int = 3  # including the one at recovery
