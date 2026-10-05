"""policy_v1 and baseline_v2 (ADR-039 D-7, ADR-040): every row of the decision table, determinism, category has no effect."""

from __future__ import annotations

from dataclasses import replace

from pulseos.jev.assessment import Assessment
from pulseos.jev.questions import CAUSES_V1
from pulseos.jev.state import build_state
from pulseos.policy import baseline, rules
from tests.unit.jev.fixtures import bundle, candidate

STATE, _ = build_state(candidate(), bundle())  # strong, rate_change, sub_cohort, impact large


def a(p=0.9, human=0.1, sev="high", category="psp_degradation"):
    probs = {s: 0.0 for s in ("low", "medium", "high", "critical")}
    probs[sev] = 1.0
    return Assessment(p, human, probs, sev, 0.8, probs["high"] + probs["critical"], {c: 0.0 for c in CAUSES_V1}, category, 0.7)


def test_decision_table_rows():
    weak_state = replace(STATE, strength="weak")
    assert rules.route(None, STATE) == ("DIGEST", "r0_unverified")
    assert rules.route(a(p=0.1), weak_state) == ("IGNORE", "r1_not_incident")
    assert rules.route(a(p=0.1), STATE) == ("DIGEST", "r2_safety_strong_signal")
    assert rules.route(a(p=0.5), STATE) == ("DIGEST", "r3_uncertain")
    assert rules.route(a(human=0.7), STATE) == ("DIGEST", "r4_needs_human")
    assert rules.route(a(sev="medium"), replace(STATE, change_type="mix_shift")) == ("DIGEST", "r5_healthy_composition")
    assert rules.route(a(sev="critical"), replace(STATE, change_type="mix_shift"))[0] == "INCIDENT"
    assert rules.route(a(), replace(STATE, locus_kind="none")) == ("DIGEST", "r6_weak_locus")
    assert rules.route(a(sev="critical", p=0.9), replace(STATE, locus_kind="none"))[0] == "INCIDENT"
    assert rules.route(a(sev="low"), STATE) == ("DIGEST", "r7_low_severity")
    assert rules.route(a(sev="medium"), STATE) == ("INCIDENT", "r8_incident")


def test_boundaries_are_inclusive_where_the_table_says_so():
    assert rules.route(a(p=0.30), replace(STATE, strength="weak"))[1] == "r3_uncertain"
    assert rules.route(a(p=0.70), STATE)[1] == "r8_incident"
    assert rules.route(a(human=0.60), STATE)[1] == "r4_needs_human"


def test_category_never_changes_the_route():
    for cause in CAUSES_V1:
        assert rules.route(a(category=cause), STATE) == ("INCIDENT", "r8_incident")
        assert rules.route(a(p=0.1, category=cause), replace(STATE, strength="weak"))[0] == "IGNORE"


def test_policy_is_deterministic():
    assert all(rules.route(a(p=0.8), STATE) == rules.route(a(p=0.8), STATE) for _ in range(5))


def test_baseline_routes_from_stage_evidence_only():
    assert baseline.route(STATE) == ("INCIDENT", "b1_strong_localized_rate_change")
    assert baseline.route(replace(STATE, change_type="volume_only", strength="weak")) == ("IGNORE", "b2_weak_composition")
    assert baseline.route(replace(STATE, impact="small"))[0] == "INCIDENT"  # baseline_v2: no impact size threshold
    assert baseline.route(replace(STATE, impact="not_provided")) == ("DIGEST", "b3_otherwise")
    assert baseline.route(replace(STATE, strength="moderate")) == ("DIGEST", "b3_otherwise")
