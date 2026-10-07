"""ADR-052 option A: the conditions a parent locus must meet (pure parts of ``cohorts.parent``)."""

from __future__ import annotations

from pulseos.cohorts.parent import PARENT_Z, acceptable, consistent


def test_parent_must_be_a_sub_cohort_on_other_dimensions():
    scope = {"customer_country": "DE"}
    assert acceptable((("psp", "psp_beta"),), False, "rate_change", scope)
    assert not acceptable((("customer_country", "DE"), ("psp", "psp_beta")), False, "rate_change", scope)  # shares DE
    assert not acceptable((), True, "rate_change", scope)  # global: no parent
    assert not acceptable((("app_version", "5.1"),), False, "new_cohort", scope)
    assert not acceptable(None, False, "rate_change", scope)


def test_scope_cohort_must_move_in_the_candidate_direction():
    assert consistent(-PARENT_Z, "down") and not consistent(-PARENT_Z + 0.1, "down")
    assert consistent(PARENT_Z + 1, "up") and not consistent(-3.0, "up") and not consistent(None, "down")
