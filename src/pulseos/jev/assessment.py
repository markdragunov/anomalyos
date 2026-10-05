"""Assessment: assembled in code from verified answers (ADR-039 D-8). Jev writes no narrative.

Severity level is the arg-max option (ties: the more severe); P(severity >= high) sums high and critical. The cause
distribution is kept for audit and evaluation; policy never routes on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pulseos.jev.questions import SEVERITIES


@dataclass(frozen=True)
class Assessment:
    incident_p: float
    needs_human_p: float
    severity_probs: dict[str, float]
    severity_level: str
    severity_confidence: float
    p_severity_high: float
    category_probs: dict[str, float]
    category_top: str
    category_confidence: float


def assess(answers: Mapping[str, Any]) -> Assessment:
    sev, cat = answers["severity"], answers["category"]
    level = max(SEVERITIES, key=lambda s: (sev["probs"][s], SEVERITIES.index(s)))
    top = max(cat["probs"], key=lambda c: (cat["probs"][c], c))
    return Assessment(answers["is_incident"]["p"], answers["needs_human"]["p"], dict(sev["probs"]), level,
                      sev["confidence"], sev["probs"]["high"] + sev["probs"]["critical"], dict(cat["probs"]), top,
                      cat["confidence"])
