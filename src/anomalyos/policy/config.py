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
    version: str = "baseline_v1"
