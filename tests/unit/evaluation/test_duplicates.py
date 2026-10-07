from __future__ import annotations

from pulseos.evaluation.duplicates import REASONS, member_outcome, reason
from pulseos.incidents.correlate import Member, OpenGroup
from pulseos.incidents.engine import CandidateInfo

H = 3600


def _c(metric="authorization_rate", locus=(), scope=(), start=10 * H):
    return CandidateInfo("c1", metric, "down", start, frozenset(locus), "INCIDENT", "d1", "all_candidates", "p",
                         scope=frozenset(scope))


def _g(*members, start=9 * H, end=11 * H):
    return OpenGroup("inc_a", (0, "inc_a"), start, end, tuple(members))


def test_member_outcomes_from_closest_to_farthest():
    psp = (("psp", "psp_beta"),)
    sepa = (("payment_method_type", "sepa_debit"),)
    # nested along an approved chain: would join
    assert member_outcome(_c(locus=psp + (("customer_country", "DE"),)), frozenset(psp), frozenset()) == "joinable"
    # pairs nest but psp -> psp x payment_method_type is not an approved chain
    assert member_outcome(_c(locus=psp + sepa), frozenset(psp), frozenset()) == "chain"
    # psp_beta x sepa (psp from the Stage 3 scope) vs gamma x sepa (scope psp): equal once scope dims are dropped
    gamma = (("psp", "psp_gamma"),)
    assert member_outcome(_c(locus=psp + sepa, scope=psp), frozenset(gamma + sepa), frozenset(gamma)) == "scope"
    assert member_outcome(_c(locus=psp + sepa, scope=psp), frozenset(gamma + (("payment_method_type", "pix"),)),
                          frozenset(gamma)) == "disjoint"


def test_reason_order_time_then_group_then_cohort():
    m = Member("m1", "authorization_rate", "down", frozenset({("psp", "psp_beta")}))
    far = _c(locus=(("psp", "psp_beta"),), start=40 * H)
    assert reason(far, _g(m), {}, 40 * H, 6 * H) == "time"
    renewal = _c(metric="renewal_success_rate", locus=(("psp", "psp_beta"),))
    assert reason(renewal, _g(m), {}, 10 * H, 6 * H) == "group"
    assert reason(_c(locus=(("psp", "psp_beta"),)), None, {}, 10 * H, 6 * H) == "not_open"
    assert set(REASONS) >= {"scope", "chain", "global", "disjoint", "joinable"}


def test_disjoint_kind_separates_conflicting_values_from_other_dimensions():
    from pulseos.evaluation.duplicates import disjoint_kind
    assert disjoint_kind(frozenset({("psp", "psp_alpha")}), frozenset({("psp", "psp_beta")})) == "conflict"
    assert disjoint_kind(frozenset({("customer_country", "DE")}), frozenset({("psp", "psp_beta")})) == "other_dims"
