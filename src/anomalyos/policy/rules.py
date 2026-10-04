"""policy_v1 (ADR-039 D-7): the decision table, first matching row wins. Pure: no I/O, no clock, no hidden state.

``category`` (including ``unknown``) never changes the route: a wrong or unknown cause must not hide an incident.
"""

from __future__ import annotations

from anomalyos.jev.assessment import Assessment
from anomalyos.jev.state import JevState
from anomalyos.policy.config import PolicyConfig


def route(assessment: Assessment | None, state: JevState, cfg: PolicyConfig = PolicyConfig()) -> tuple[str, str]:
    """(route, rule id). ``assessment`` is ``None`` when the response was not verified (row 0)."""
    if assessment is None:
        return "DIGEST", "r0_unverified"
    p = assessment.incident_p
    if p < cfg.ignore_below:
        if state.strength == "strong" and state.impact in ("medium", "large"):
            return "DIGEST", "r2_safety_strong_signal"
        return "IGNORE", "r1_not_incident"
    if p < cfg.incident_at:
        return "DIGEST", "r3_uncertain"
    if assessment.needs_human_p >= cfg.needs_human_at:
        return "DIGEST", "r4_needs_human"
    if state.change_type in ("volume_only", "mix_shift") and assessment.p_severity_high < cfg.composition_high_severity:
        return "DIGEST", "r5_healthy_composition"
    if state.locus_kind in ("none", "not_provided") or state.change_type in ("insufficient_data", "not_provided"):
        if not (assessment.severity_level == "critical" and p >= cfg.critical_override):
            return "DIGEST", "r6_weak_locus"
    if assessment.severity_level == "low":
        return "DIGEST", "r7_low_severity"
    return "INCIDENT", "r8_incident"
