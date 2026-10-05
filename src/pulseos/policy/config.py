"""Versioned policy configuration (ADR-039 D-7). Values were fixed at Gate 0; they are tuned only at Gate 1, on DEV
seeds 1-10, and every change bumps the version."""

from __future__ import annotations

from dataclasses import dataclass

ROUTES = ("IGNORE", "DIGEST", "INCIDENT")


@dataclass(frozen=True)
class PolicyConfig:
    version: str = "policy_v1"
    ignore_below: float = 0.30  # is_incident below this -> IGNORE (unless the safety rule applies)
    incident_at: float = 0.70  # is_incident at or above this may reach INCIDENT
    needs_human_at: float = 0.60  # needs_human at or above this -> DIGEST
    composition_high_severity: float = 0.50  # healthy composition needs P(severity >= high) >= this
    critical_override: float = 0.85  # weak locus support is overridden only by critical severity and this is_incident


@dataclass(frozen=True)
class BaselineConfig:
    """baseline_v2: tuned on DEV seeds 1-10 (scripts/tune_stage5_dev.py, ADR-040) — maximum incident recall at
    INCIDENT with (unmatched + suppress) -> INCIDENT <= 0.10 per day. v1: moderate strength, medium impact, partial
    locus share, rate-like change, never tuned."""
    version: str = "baseline_v2"
    strength_min: str = "strong"
    change_types: tuple[str, ...] = ("rate_change", "new_cohort", "mixed")
    impact_min: str = "negligible"  # i.e. impact must be provided; no size threshold won
    locus_share_min: str = "not_provided"  # no locus-share threshold won
