"""Hypotheses from the closed cause vocabulary (ADR-049 D-4): control prior, update rule, ranking.

Input: the anchor candidate's metric, direction, locus dimensions and Stage 4 label. Output: ``Hypothesis`` records.
Invariants: causes only from vocabulary v1; contradicting evidence is kept, never dropped; ``normal_variation`` and
``unknown`` are always present; statements never claim causation ("consistent with", "evidence against").
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from pulseos.jev.questions import CAUSES_V1

# (metric, direction) -> (cause by locus dimension, causes of the same family)
PRIOR_TABLE: dict[tuple[str, str], tuple[dict[str, str], tuple[str, ...]]] = {
    ("authorization_rate", "down"): ({"psp": "psp_degradation", "payment_method_type": "payment_method_degradation",
                                      "customer_country": "issuer_or_country_degradation",
                                      "card_brand": "issuer_or_country_degradation"},
                                     ("psp_degradation", "payment_method_degradation", "issuer_or_country_degradation",
                                      "fraud_attack", "data_pipeline_issue")),
    ("checkout_conversion_rate", "down"): ({"platform": "checkout_regression", "app_version": "checkout_regression"},
                                           ("checkout_regression", "psp_degradation", "data_pipeline_issue")),
    ("renewal_success_rate", "down"): ({"psp": "renewal_job_failure"}, ("renewal_job_failure", "dunning_failure",
                                                                        "psp_degradation")),
    ("dunning_recovery_rate", "down"): ({"psp": "dunning_failure"}, ("dunning_failure", "renewal_job_failure")),
    ("refund_count", "up"): ({"customer_country": "refund_process_change"}, ("refund_process_change", "duplicate_charging")),
    ("duplicate_charge_rate", "up"): ({"platform": "duplicate_charging"}, ("duplicate_charging",)),
    ("fraud_flag_rate", "up"): ({}, ("fraud_attack",)),
    ("attempt_volume", "up"): ({}, ("fraud_attack", "normal_variation")),
    ("attempt_volume", "down"): ({"psp": "psp_degradation"}, ("psp_degradation", "data_pipeline_issue")),
    ("subscription_cancellation_rate", "up"): ({"customer_country": "pricing_or_plan_change"}, ("pricing_or_plan_change",)),
    ("late_arrival_share", "up"): ({"psp": "data_pipeline_issue"}, ("data_pipeline_issue",)),
}
# metric families whose rise itself names the cause (no locus needed)
DIRECT = {("fraud_flag_rate", "up"): "fraud_attack", ("duplicate_charge_rate", "up"): "duplicate_charging",
          ("subscription_cancellation_rate", "up"): "pricing_or_plan_change", ("late_arrival_share", "up"): "data_pipeline_issue",
          ("renewal_success_rate", "down"): "renewal_job_failure", ("dunning_recovery_rate", "down"): "dunning_failure",
          ("refund_count", "up"): "refund_process_change"}
STATUSES = ("open", "supported", "contradicted", "insufficient_evidence")


def prior(metric: str, direction: str, locus_dims: set[str], change_type: str) -> dict[str, int]:
    """3 = match on the locus dimension (or a direct family), 1 = same family only, 0 = none."""
    by_dim, family = PRIOR_TABLE.get((metric, direction), ({}, ()))
    out = {c: 1 for c in family}
    for dim, cause in by_dim.items():
        if dim in locus_dims:
            out[cause] = 3
    if (metric, direction) in DIRECT:
        out[DIRECT[(metric, direction)]] = 3
    if change_type in ("mix_shift", "volume_only"):
        out["normal_variation"] = 3
    return out


@dataclass
class Hypothesis:
    hypothesis_id: str
    cause: str
    prior: int
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    support_probability: float | None = None  # Jev noul (fake now) or None for the control
    confidence: float | None = None
    status: str = "open"
    next_question: str | None = None

    @property
    def score(self) -> int:
        return self.prior + len(self.supporting) - 2 * len(self.contradicting)

    def refresh(self) -> None:
        s, c = len(self.supporting), len(self.contradicting)
        if c >= 1 and c > s:
            self.status = "contradicted"
        elif s >= 2 and c == 0:
            self.status = "supported"
        else:
            self.status = "open"


def initial(investigation_id: str, priors: dict[str, int], max_hypotheses: int) -> list[Hypothesis]:
    ranked = sorted((c for c, w in priors.items() if w > 0 and c not in ("normal_variation", "unknown")),
                    key=lambda c: (-priors[c], c))[:max_hypotheses]
    causes = ranked + ["normal_variation", "unknown"]
    assert set(causes) <= set(CAUSES_V1)
    return [Hypothesis("hyp_" + hashlib.sha256(f"{investigation_id}|{c}".encode()).hexdigest()[:12], c, priors.get(c, 0))
            for c in causes]


def ranking(hyps: list[Hypothesis]) -> list[Hypothesis]:
    return sorted(hyps, key=lambda h: (-h.score, -h.prior, h.cause))
