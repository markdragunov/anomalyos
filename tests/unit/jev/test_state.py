"""JevState v1 (ADR-039 D-1, D-2): closed enums, provenance, canonical hash, size budget."""

from __future__ import annotations

import json

import pytest

from pulseos.jev import buckets
from pulseos.jev.state import (MAX_STATE_BYTES, STATE_FIELDS, StateError, build_state, canonical_json, state_dict,
                                 state_hash)
from tests.unit.jev.fixtures import bundle, candidate


def test_buckets_edges():
    assert [buckets.relative_change(x, "down") for x in (-0.01, -0.03, -0.1, -0.3, -0.6)] == \
        ["none", "slight", "moderate", "severe", "collapse"]
    assert [buckets.relative_change(x, "up") for x in (0.2, 0.7, 2.0, 5.0, None)] == \
        ["slight", "moderate", "severe", "extreme", "extreme"]
    assert [buckets.strength(z) for z in (3.0, 4.0, 7.9, 8.0, None)] == ["weak", "moderate", "moderate", "strong", "not_provided"]
    assert [buckets.share(x) for x in (0.1, 0.3, 0.6, 0.95)] == ["minor", "partial", "most", "nearly_all"]
    assert [buckets.cohorts_moved(n) for n in (0, 1, 5, 6)] == ["none", "one", "few", "many"]
    assert [buckets.impact_rate(v, 1.0) for v in (-3, 10, 30, 60, None)] == ["negligible", "small", "medium", "large", "not_provided"]
    assert buckets.impact_rate(240, 24.0) == "small" and buckets.impact_rate(5, 0) == "not_provided"  # per hour
    assert buckets.detection_rule(("cusum", "z_persistence")) == "both"


def test_state_from_candidate_and_bundle():
    s, prov = build_state(candidate(), bundle(), [candidate(anomaly_id="anom_x", evidence_ids=("evd_x",))])
    assert s.metric_family == "approval" and s.scope_dims == ("psp",) and s.relative_change == "severe"
    assert s.strength == "strong" and s.change_type == "rate_change" and s.locus_kind == "sub_cohort"
    assert s.locus_dims == ("payment_method_type",) and s.locus_share == "nearly_all"
    assert s.controls == "present" and s.related_metrics == "agree" and s.impact == "large"  # 340 lost in one hour
    assert s.concurrent_alerts == "one" and s.calendar_context == "not_provided"
    assert prov["locus_share"] == ("evd_top",) and prov["concurrent_alerts"] == ("evd_x",)
    assert set(prov) == set(STATE_FIELDS)


def test_state_holds_no_free_text_numbers_ids_or_timestamps():
    s, _ = build_state(candidate(), bundle())
    text = canonical_json(s).decode()
    for forbidden in ("psp_beta", "sepa_debit", "anom_", "evd_", "evb_", "0.62", str(candidate()["detected_at"]),
                      "run_id", "scenario", "seed", "truth"):
        assert forbidden not in text, forbidden
    assert all(isinstance(v, (str, list)) for v in state_dict(s).values())
    assert all(isinstance(x, str) for v in state_dict(s).values() if isinstance(v, list) for x in v)


def test_missing_stage4_evidence_is_explicit():
    s, prov = build_state(candidate(), None)
    assert s.change_type == s.locus_kind == s.impact == "not_provided" and s.locus_dims == ()
    assert prov["impact"] == ()


def test_whole_scope_and_new_cohort_loci():
    s, _ = build_state(candidate(), bundle(locus={"cohort": [["psp", "psp_beta"]], "is_scope": True}))
    assert s.locus_kind == "whole_scope" and s.locus_share == "nearly_all" and s.locus_dims == ()
    s, _ = build_state(candidate(), bundle(label={"value": "new_cohort"}))
    assert s.locus_kind == "new_cohort"


def test_canonical_hash_is_stable_and_schema_bound():
    a, _ = build_state(candidate(), bundle())
    b, _ = build_state(candidate(observed=0.61), bundle())  # same buckets -> same state
    assert canonical_json(a) == canonical_json(b) and state_hash(a) == state_hash(b)
    assert state_hash(a) != state_hash(a, "jev_state_v1")
    assert json.loads(canonical_json(a)) == state_dict(a)


def test_unknown_metric_and_size_budget():
    with pytest.raises(StateError):
        build_state(candidate(metric="mystery_rate"), None)
    s, _ = build_state(candidate(), bundle())
    assert len(canonical_json(s)) < MAX_STATE_BYTES // 2
