"""The engine fold (ADR-041 D-0 … D-4): create, join, digest and promotion, recovery with hysteresis, signal return,
human commands, deterministic replay. Hand-built events; no ClickHouse, no ground truth."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from pulseos.incidents.config import IncidentConfig
from pulseos.incidents.engine import CandidateInfo, Engine, Event
from pulseos.incidents.lifecycle import TransitionError

H = 3600


def info(cid, route="INCIDENT", locus=(("psp", "b"),), metric="authorization_rate", direction="down", start=0, **kw):
    return CandidateInfo(cid, metric, direction, start, frozenset(locus), route, f"dec_{cid}", "test", "policy_v1",
                         verified=kw.pop("verified", True), incident_p=0.9, severity_level="high",
                         categories=("psp_degradation", "unknown", "normal_variation"), evidence_ids=(f"evd_{cid}",),
                         window_end=start + H, **kw)


def det(t, i):
    return Event(t, "detected", i.candidate_id, info=i)


def run(events, cfg=IncidentConfig()):
    return Engine(cfg).run(events)


def test_related_candidates_form_one_incident_and_unrelated_ones_do_not():
    r = run([det(H, info("c1")), det(2 * H, info("c2", locus=(("psp", "b"), ("customer_country", "DE")), start=H)),
             det(2 * H, info("c3", locus=(("customer_country", "FR"),), start=H))])
    assert len(r.incidents) == 2
    big = next(i for i in r.incidents.values() if len(i["linked_anomalies"]) == 2)
    assert big["linked_anomalies"] == ["c1", "c2"] and big["status"] == "DETECTED"
    assert big["affected_dimensions"] == [["customer_country", "DE"], ["psp", "b"]]
    assert big["jev_status"] == "not_evaluated" and big["epistemic"]["category"] == "INFERRED"
    kinds = sorted(l["kind"] for l in r.links)
    assert kinds.count("create") == 2 and "join" in kinds and "related_to" in kinds


def test_ignore_creates_nothing_and_digest_is_grouped_then_promoted():
    r = run([det(H, info("c1", route="IGNORE")), det(H, info("d1", route="DIGEST")),
             det(2 * H, info("d2", route="DIGEST", locus=(("psp", "b"), ("customer_country", "DE")), start=H))])
    assert r.incidents == {} and list(r.digest_groups.values()) == [["d1", "d2"]]
    up = replace(info("d2", locus=(("psp", "b"), ("customer_country", "DE")), start=H), route="INCIDENT")
    r = run([det(H, info("d1", route="DIGEST")),
             det(2 * H, info("d2", route="DIGEST", locus=(("psp", "b"), ("customer_country", "DE")), start=H)),
             Event(8 * H, "checkpoint", "d2", info=up)])
    (inc,) = r.incidents.values()
    assert inc["linked_anomalies"] == ["d1", "d2"]
    assert sum(1 for d in r.digest_items if d["promoted_to"] == inc["incident_id"]) == 2
    assert r.incident_events[0]["reason"] == "digest_promoted" and r.incident_events[0]["actor"] == "policy"


def test_digest_supports_an_open_incident_without_paging():
    r = run([det(H, info("c1")), det(2 * H, info("d1", route="DIGEST", start=H))])
    (inc,) = r.incidents.values()
    assert inc["linked_anomalies"] == ["c1", "d1"] and any(l["kind"] == "support" for l in r.links)


def test_recovery_after_hysteresis_and_signal_return():
    cfg = IncidentConfig(hysteresis_s=2 * H)
    base = [det(H, info("c1")), Event(5 * H, "recovered", "c1"), Event(6 * H, "recovery_check", "c1")]
    assert next(iter(run(base, cfg).incidents.values()))["status"] == "DETECTED"  # 1 h < hysteresis
    r = run(base + [Event(7 * H, "recovery_check", "c1")], cfg)
    assert next(iter(r.incidents.values()))["status"] == "RECOVERING"
    # a signal that returns within the gap G of the recovery re-opens the incident (no flapping close) ...
    back = run(base + [Event(7 * H, "recovery_check", "c1"), det(8 * H, info("c2", start=int(5.5 * H)))], cfg)
    (inc,) = back.incidents.values()
    assert inc["status"] == "INVESTIGATING" and back.incident_events[-1]["reason"] == "signal_returned"
    # ... one that starts later than G after the recovery is a new incident
    later = run(base + [Event(7 * H, "recovery_check", "c1"), det(9 * H, info("c3", start=8 * H))], cfg)
    assert sorted(i["status"] for i in later.incidents.values()) == ["DETECTED", "RECOVERING"]


def test_human_commands_and_no_system_closure():
    base = [det(H, info("c1"))]
    iid = next(iter(run(base).incidents))
    r = run(base + [Event(2 * H, "human", incident_id=iid, actor="human", command="acknowledge", reason="on it"),
                    Event(3 * H, "human", incident_id=iid, actor="human", command="escalate", reason="customer impact"),
                    Event(9 * H, "human", incident_id=iid, actor="human", command="resolve", reason="PSP fixed")])
    assert [e["status"] for e in r.incident_events] == ["DETECTED", "ACKNOWLEDGED", "ESCALATED", "RESOLVED"]
    assert all(e["actor"] and e["reason"] for e in r.incident_events)
    with pytest.raises(TransitionError):
        run(base + [Event(2 * H, "human", incident_id=iid, actor="system", command="resolve", reason="auto")])
    with pytest.raises(TransitionError):
        run(base + [Event(2 * H, "human", incident_id=iid, actor="human", command="resolve", reason="")])


def test_merge_is_a_human_command():
    evs = [det(H, info("c1")), det(H, info("c2", locus=(("customer_country", "FR"),)))]
    ids = sorted(run(evs).incidents)
    r = run(evs + [Event(2 * H, "human", incident_id=ids[1], target_id=ids[0], actor="human", command="merge", reason="same root")])
    assert r.incidents[ids[1]]["status"] == "DISMISSED" and len(r.incidents[ids[0]]["linked_anomalies"]) == 2
    with pytest.raises(TransitionError):
        run(evs + [Event(2 * H, "human", incident_id=ids[1], target_id=ids[0], actor="policy", command="merge", reason="x")])


def test_replay_is_deterministic_regardless_of_input_order():
    evs = [det(H, info("c1")), det(2 * H, info("c2", start=H)), Event(5 * H, "recovered", "c1"),
           Event(5 * H, "recovered", "c2"), Event(7 * H, "recovery_check", "c2"), det(3 * H, info("d1", route="DIGEST"))]
    a, b = run(evs), run(list(reversed(evs)))
    assert a.incident_events == b.incident_events and a.links == b.links and a.digest_items == b.digest_items
    assert json.loads(a.incident_events[-1]["snapshot_json"])["linked_anomalies"] == ["c1", "c2", "d1"]


def test_a_checkpoint_refines_the_member_locus():
    # Gate 1 fix (b): c1 starts global, a checkpoint localizes it to psp b; a later psp b candidate then joins
    g = info("c1", locus=())
    refined = info("c1", locus=(("psp", "b"),))
    r = run([det(H, g), Event(3 * H, "checkpoint", "c1", info=refined), det(4 * H, info("c2", start=3 * H))])
    assert len(r.incidents) == 1 and next(iter(r.incidents.values()))["linked_anomalies"] == ["c1", "c2"]
    without = run([det(H, g), det(4 * H, info("c2", start=3 * H))])
    assert len(without.incidents) == 2  # the global member alone does not anchor c2


def test_a_refined_locus_must_still_nest_with_its_anchor():
    # ADR-042 (the seed-11 case): a global candidate joins c1's incident, then its locus is refined to an unrelated
    # PSP; the refinement is not accepted, so it cannot become an entry point for that PSP's candidates
    c1 = info("c1", locus=(("psp", "a"), ("customer_country", "GB")))
    g = info("g", locus=(), start=H)
    elsewhere = info("g", locus=(("psp", "b"),), start=H)
    r = run([det(H, c1), det(2 * H, g), Event(3 * H, "checkpoint", "g", info=elsewhere),
             det(4 * H, info("x", locus=(("psp", "b"), ("customer_country", "FR")), start=3 * H))])
    assert len(r.incidents) == 2
    # a refinement that still nests with the anchor is accepted
    inside = info("g", locus=(("psp", "a"),), start=H)
    r = run([det(H, c1), det(2 * H, g), Event(3 * H, "checkpoint", "g", info=inside),
             det(4 * H, info("y", locus=(("psp", "a"), ("card_brand", "visa")), start=3 * H))])
    assert len(r.incidents) == 1  # y nests only with g's refined locus (psp a ⊂ psp×card_brand), not with c1
