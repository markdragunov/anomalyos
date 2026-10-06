"""The check library (ADR-049 D-2): closed-set checks built from the incident's own evidence, each with the outcome
every cause predicts, and the shortlist with the disconfirming rule.

Kinds: ``sibling`` (the locus vs. a cohort that differs in one dimension — does the change follow that dimension?),
``related`` (a related metric on the locus), ``deploy`` and ``status`` (side files: ``present`` supports, absence never
contradicts). Arguments come only from closed sets: loci, cohort values seen in the pooled tables, the related-metric
table, the service and PSP lists.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pulseos.investigation.hypotheses import Hypothesis, ranking

CAUSE_DIM = {"psp_degradation": "psp", "payment_method_degradation": "payment_method_type",
             "issuer_or_country_degradation": "customer_country", "checkout_regression": "app_version",
             "renewal_job_failure": "psp", "dunning_failure": "psp", "data_pipeline_issue": "psp",
             "duplicate_charging": "platform", "refund_process_change": "customer_country",
             "pricing_or_plan_change": "customer_country", "fraud_attack": "customer_country"}
M, U = "moved", "unchanged"
# (anchor metric, direction) -> related metric -> (expected direction, {cause: predicted outcome})
RELATED: dict[tuple[str, str], dict[str, tuple[str, dict[str, str]]]] = {
    ("authorization_rate", "down"): {
        "checkout_conversion_rate": ("down", {"psp_degradation": M, "payment_method_degradation": M,
                                              "issuer_or_country_degradation": M, "normal_variation": U}),
        "fraud_flag_rate": ("up", {"fraud_attack": M, "psp_degradation": U, "payment_method_degradation": U,
                                   "issuer_or_country_degradation": U, "normal_variation": U}),
        "attempt_volume": ("up", {"fraud_attack": M, "psp_degradation": U, "issuer_or_country_degradation": U}),
        "late_arrival_share": ("up", {"data_pipeline_issue": M, "psp_degradation": U, "payment_method_degradation": U,
                                      "issuer_or_country_degradation": U})},
    ("checkout_conversion_rate", "down"): {
        "authorization_rate": ("down", {"checkout_regression": U, "psp_degradation": M}),
        "late_arrival_share": ("up", {"data_pipeline_issue": M, "checkout_regression": U})},
    ("renewal_success_rate", "down"): {
        "dunning_recovery_rate": ("down", {"dunning_failure": M}),
        "authorization_rate": ("down", {"renewal_job_failure": U, "dunning_failure": U, "psp_degradation": M})},
    ("dunning_recovery_rate", "down"): {
        "authorization_rate": ("down", {"dunning_failure": U, "renewal_job_failure": U, "psp_degradation": M})},
    ("refund_count", "up"): {"authorization_rate": ("down", {"refund_process_change": U}),
                             "duplicate_charge_rate": ("up", {"duplicate_charging": M, "refund_process_change": U})},
    ("duplicate_charge_rate", "up"): {"refund_count": ("up", {"duplicate_charging": M})},
    ("fraud_flag_rate", "up"): {"attempt_volume": ("up", {"fraud_attack": M}),
                                "authorization_rate": ("down", {"fraud_attack": M})},
    ("attempt_volume", "up"): {"fraud_flag_rate": ("up", {"fraud_attack": M, "normal_variation": U}),
                               "authorization_rate": ("down", {"fraud_attack": M, "normal_variation": U})},
    ("attempt_volume", "down"): {"authorization_rate": ("down", {"psp_degradation": M, "normal_variation": U})},
    ("subscription_cancellation_rate", "up"): {"authorization_rate": ("down", {"pricing_or_plan_change": U})},
    ("late_arrival_share", "up"): {"authorization_rate": ("down", {"data_pipeline_issue": M})},
}
SERVICE_OF = {"renewal_job_failure": "renewal_job", "dunning_failure": "dunning_service",
              "refund_process_change": "refund_service", "pricing_or_plan_change": "pricing_service",
              "duplicate_charging": "api_gateway"}
STATUS_OF = {"psp_degradation": "authorization", "payment_method_degradation": "local_methods",
             "data_pipeline_issue": "webhooks"}
KIND_ORDER = {"sibling": 0, "channel": 0, "related": 1, "status": 2, "deploy": 3}
# ADR-050 follow-up: approval on the checkout channel separates renewal-side causes from payment-side ones
CHANNEL_SPLIT = {"renewal_job_failure": U, "dunning_failure": U, "normal_variation": U, "psp_degradation": M,
                 "issuer_or_country_degradation": M, "payment_method_degradation": M, "fraud_attack": M,
                 "data_pipeline_issue": M}


@dataclass(frozen=True)
class Check:
    check_id: str
    kind: str
    tool: str
    args: tuple
    predictions: tuple[tuple[str, str], ...]  # (cause, moved | unchanged | present:<component or service>)

    @property
    def key(self) -> tuple:
        return (self.tool, self.args)

    def predicted(self, cause: str) -> str | None:
        return dict(self.predictions).get(cause)


def _id(*parts) -> str:
    return "chk_" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12]


def build(metric: str, direction: str, locus: dict[str, str], siblings: dict[str, list[str]], causes: list[str],
          platform_release: str | None, psps: list[str]) -> list[Check]:
    """Checks for one incident. ``siblings``: dimension -> other values of that dimension with support."""
    out: list[Check] = []
    loc = tuple(sorted(locus.items()))
    for dim, values in sorted(siblings.items()):
        for value in values[:1]:  # the best-supported other value only: the check set stays small
            sib = tuple(sorted({**locus, dim: value}.items()))
            preds = []
            for c in causes:
                if c == "normal_variation":
                    preds.append((c, U))
                elif CAUSE_DIM.get(c) == dim:
                    preds.append((c, U))
                elif CAUSE_DIM.get(c) in locus:
                    preds.append((c, M))
            args = (metric, loc, sib, direction)
            out.append(Check(_id("sibling", *args), "sibling", "compare_cohorts", args, tuple(preds)))
    if metric == "authorization_rate" and "channel" not in locus:
        preds = tuple((c, p) for c, p in sorted(CHANNEL_SPLIT.items()) if c in causes)
        if any(p == U for _, p in preds) and any(p == M for _, p in preds):
            args = (metric, tuple(sorted({**locus, "channel": "checkout"}.items())), direction)
            out.append(Check(_id("channel", *args), "channel", "get_metric_history", args, preds))
    for rel, (rdir, table) in sorted(RELATED.get((metric, direction), {}).items()):
        preds = tuple((c, p) for c, p in sorted(table.items()) if c in causes)
        if preds:
            args = (rel, loc, rdir)
            out.append(Check(_id("related", *args), "related", "get_related_metrics", args, preds))
    for cause, service in sorted(SERVICE_OF.items()):
        if cause in causes:
            args = (service, "onset_2h")
            out.append(Check(_id("deploy", *args), "deploy", "get_deployments", args, ((cause, f"present:{service}"),)))
    if "checkout_regression" in causes and platform_release:
        args = (platform_release, "onset_2h")
        out.append(Check(_id("deploy", *args), "deploy", "get_deployments", args,
                         (("checkout_regression", f"present:{platform_release}"),)))
    status_causes = [(c, comp) for c, comp in sorted(STATUS_OF.items()) if c in causes]
    for psp in sorted(psps):
        if status_causes:
            args = (psp, "start_6h")
            out.append(Check(_id("status", *args), "status", "check_psp_status", args,
                             tuple((c, f"present:{comp}") for c, comp in status_causes)))
    return out


def disconfirming(check: Check, leader: Hypothesis) -> bool:
    return check.kind in ("sibling", "channel", "related") and check.predicted(leader.cause) in (M, U)


def shortlist(checks: list[Check], done: set[tuple], hyps: list[Hypothesis], size: int) -> list[Check]:
    """≤ ``size`` unrun checks ranked by how well they separate the top-3 hypotheses; always one that could contradict
    the leader if any is left (otherwise the loop stops with ``no_disconfirming_check``)."""
    top = ranking(hyps)[:3]
    leader = top[0]

    def key(c: Check):
        preds = [c.predicted(h.cause) for h in top]
        sep = len({p for p in preds if p is not None})
        return (-sep, -(c.predicted(leader.cause) is not None), KIND_ORDER[c.kind], c.check_id)

    pool = sorted((c for c in checks if c.key not in done), key=key)
    sl = pool[:size]
    if sl and not any(disconfirming(c, leader) for c in sl):
        extra = next((c for c in pool[size:] if disconfirming(c, leader)), None)
        if extra is not None:
            sl = sl[:-1] + [extra]
    return sl


def update(hyps: list[Hypothesis], check: Check, observed: str | frozenset, evidence_id: str) -> None:
    """Metric checks (``observed`` an outcome) support a matching prediction and contradict an opposed one; side checks
    (``observed`` the set of ``present:<x>`` tokens found) only support."""
    for h in hyps:
        p = check.predicted(h.cause)
        if p is None:
            continue
        if p.startswith("present:"):
            if isinstance(observed, frozenset) and p in observed:
                h.supporting.append(evidence_id)
        elif observed in (M, U, "opposite"):
            if observed == p:
                h.supporting.append(evidence_id)
            else:
                h.contradicting.append(evidence_id)
        h.refresh()
