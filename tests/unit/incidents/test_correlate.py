"""Correlation rules (ADR-041 D-2): time, nested loci along approved chains, metric groups, ambiguous global loci."""

from __future__ import annotations

import pytest

from pulseos.incidents.config import IncidentConfig
from pulseos.incidents.correlate import Member, OpenGroup, decide, groups, groups_compatible, nested

H = 3600
CFG = IncidentConfig()


def L(**kw):
    return frozenset(kw.items())


def group(gid, members, start=0, end=4 * H, created=0):
    return OpenGroup(gid, (created, gid), start, end, tuple(members))


def test_nesting_follows_the_approved_chains_only():
    assert nested(L(psp="b"), L(psp="b", customer_country="DE")) == (True, False)
    assert nested(L(customer_country="DE"), L(customer_country="DE", payment_method_type="sepa_debit")) == (True, False)
    assert nested(L(psp="b", customer_country="DE"), L(psp="b", customer_country="DE", platform="web")) == (True, False)
    assert nested(L(psp="b"), L(psp="b", customer_country="DE", platform="web")) == (True, False)  # transitive
    assert nested(L(psp="b"), L(psp="b", payment_method_type="sepa_debit"))[0] is False  # not an approved chain
    assert nested(L(psp="b"), L(psp="g", customer_country="DE"))[0] is False  # values differ
    assert nested(L(), L(psp="b")) == (True, True) and nested(L(psp="b"), L(psp="b")) == (True, False)


def test_metric_groups():
    assert groups("attempt_volume", "up") == {"fraud"} and groups("attempt_volume", "down") == {"payments"}
    assert groups_compatible(groups("authorization_rate", "down"), groups("checkout_conversion_rate", "down")) == {"payments"}
    assert not groups_compatible(groups("refund_count", "up"), groups("renewal_success_rate", "down"))
    assert groups_compatible(groups("late_arrival_share", "up"), groups("renewal_success_rate", "down"))


def test_join_needs_time_cohort_and_group():
    inc = group("i1", [Member("c1", "authorization_rate", "down", L(psp="b"))])
    assert decide(Member("c2", "authorization_rate", "down", L(psp="b", customer_country="DE")), H, 2 * H, [inc], CFG).target == "i1"
    far = decide(Member("c3", "authorization_rate", "down", L(psp="b")), 10 * H, 11 * H, [inc], CFG)
    assert far.target is None  # beyond the gap
    within_gap = decide(Member("c3", "authorization_rate", "down", L(psp="b")), 4 * H + 1800, 6 * H, [inc], CFG)
    assert within_gap.target == "i1" and within_gap.evidence["time"] == "gap"
    other_group = decide(Member("c4", "renewal_success_rate", "down", L(psp="b")), H, 2 * H, [inc], CFG)
    assert other_group.target is None and other_group.related == ()
    not_nested = decide(Member("c5", "authorization_rate", "down", L(customer_country="FR")), H, 2 * H, [inc], CFG)
    assert not_nested.target is None and not_nested.related == ("i1",)


def test_oldest_incident_wins_ties():
    a = group("ia", [Member("c1", "authorization_rate", "down", L(psp="b"))], created=5)
    b = group("ib", [Member("c2", "authorization_rate", "down", L(psp="b"))], created=1)
    assert decide(Member("c3", "authorization_rate", "down", L(psp="b")), H, 2 * H, [a, b], CFG).target == "ib"


@pytest.mark.parametrize("method", ["ideal", "sepa_debit"])
def test_campaign_and_outage_never_merge(method):
    # correlated_unrelated_anomalies: a country campaign (volume up) and an iDEAL or SEPA outage (approval down)
    campaign = group("i_campaign", [Member("vol", "attempt_volume", "up", L(customer_country="DE"))], created=0)
    outage = group("i_outage", [Member("auth", "authorization_rate", "down", L(payment_method_type=method))], created=1)
    # the outage candidate does not join the campaign (groups share "fraud", but loci are not nested)
    m = decide(Member("auth", "authorization_rate", "down", L(payment_method_type=method)), H, 2 * H, [campaign], CFG)
    assert m.target is None
    # a global approval candidate overlapping both is ambiguous and stays separate, related to both
    g = decide(Member("glob", "authorization_rate", "down", L()), H, 2 * H, [campaign, outage], CFG)
    assert g.target is None and set(g.related) == {"i_campaign", "i_outage"}
    # with only one candidate incident the global locus links unambiguously
    assert decide(Member("glob", "authorization_rate", "down", L()), H, 2 * H, [outage], CFG).target == "i_outage"
