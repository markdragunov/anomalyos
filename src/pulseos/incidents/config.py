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
# Option B-chains (ADR-052): edges the duplicate diagnosis found at least twice (ADR-051)
CHAIN_EDGES_PLUS: tuple[tuple[frozenset[str], frozenset[str]], ...] = CHAIN_EDGES + tuple(
    (frozenset(a), frozenset(b)) for a, b in (
        (("app_version",), ("platform", "app_version")),
        (("psp",), ("psp", "platform")),
        (("psp", "platform"), ("psp", "customer_country", "platform")),
        (("customer_country",), ("customer_country", "platform")),
        (("psp", "customer_country"), ("psp", "customer_country", "card_brand")),
    ))
NESTING_MODES = ("chains", "chains_plus", "pairs")  # ADR-041 chains · B-chains · B-pairs (ADR-052)

# Metric groups (D-2), keyed by (metric, direction); None = either direction. "ingestion" is compatible with all.
METRIC_GROUPS: dict[tuple[str, str | None], frozenset[str]] = {
    ("authorization_rate", None): frozenset({"payments"}),  # not "fraud": a volume rise must not join an approval drop (ADR-042)
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
# Option C (ADR-052): an approval drop also belongs to "subscriptions", so renewal / dunning drops can join it
RENEWAL_WITH_APPROVAL = frozenset({"payments", "subscriptions"})
IMPACT_METRICS = ("authorization_rate", "checkout_conversion_rate")


@dataclass(frozen=True)
class IncidentConfig:
    version: str = "incidents_v4"  # v2: ADR-042; v3: G / H defaults (ADR-051); v4: chains plus diagnosed edges (ADR-052)
    gap_s: int = 6 * HOUR  # D-2 rule 1, chosen on seeds 1-10 over 0, 1, 3, 6 h (ADR-042)
    hysteresis_s: int = 0  # D-4 quiet time before RECOVERING, chosen over 0, 1, 3 h (ADR-042)
    checkpoints_s: tuple[int, ...] = (6 * HOUR, 24 * HOUR)  # D-5, after detected_at, while the episode is open
    max_checkpoints: int = 3  # including the one at recovery
    nesting: str = "chains_plus"  # NESTING_MODES; owner choice at Gate 2 (ADR-052): "pairs" merged a campaign + outage
    parent_locus: bool = False  # ADR-052 option A: correlation may also use the candidate's parent locus
    renewal_with_approval: bool = False  # ADR-052 option C
    ingestion_anchors_others: bool = True  # ADR-053: False = an ingestion-only member anchors only ingestion candidates
