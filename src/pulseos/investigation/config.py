"""Versioned investigation configuration (ADR-049 D-1, D-2, D-3, D-5). Thresholds are fixed; only the budget defaults
are tuned, on DEV seeds 1-10 (D-9). Every change bumps the version."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InvestigationConfig:
    version: str = "investigation_v1"
    # Stage A narrowing (D-1)
    chunk_size: int = 40
    split: int = 4
    leaf_size: int = 10
    max_depth: int = 3
    max_leaves: int = 4
    # Stage B loop (D-2)
    shortlist_size: int = 5
    choice_confidence_min: float = 0.5
    max_hypotheses: int = 5
    # outcome thresholds (D-3)
    z_moved: float = 2.0
    z_unchanged: float = 1.0
    min_support: int = 30
    # budgets (D-5)
    max_tool_calls: int = 12
    max_jev_calls: int = 40
    max_evidence: int = 60
    max_wall_s: float = 120.0
    query_timeout_s: int = 10
