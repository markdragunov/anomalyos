"""Impact anchor without double counting (ADR-041 D-6) and append-only incident storage (D-7)."""

from __future__ import annotations

from dataclasses import replace

from pulseos.incidents import impact, storage
from pulseos.incidents.engine import CandidateInfo, Engine, Event

H = 3600


def info(cid, metric="authorization_rate", locus=(("psp", "b"),), value=100.0, start=0, window_end=H):
    imp = {"measure": "lost_successes", "value": value, "interval": [value * 0.8, value * 1.2], "epistemic": "estimated"}
    return CandidateInfo(cid, metric, "down", start, frozenset(locus), "INCIDENT", f"dec_{cid}", "t", "policy_v1",
                         impact=imp, window_end=window_end)


def test_one_anchor_the_most_specific_approval_candidate():
    members = [info("glob", locus=(), value=500.0), info("psp", value=120.0),
               info("pair", locus=(("psp", "b"), ("customer_country", "DE")), value=90.0),
               info("vol", metric="attempt_volume", locus=(("psp", "b"), ("customer_country", "DE"), ("platform", "web")))]
    a = impact.anchor(members)
    assert a.candidate_id == "pair"  # volume is not an impact metric; the global one would double count
    est = impact.lost_successes(a, 0, 4 * H)  # analysis window 1 h, incident 4 h
    assert est["value"] == 360.0 and est["interval"] == [288.0, 432.0] and est["epistemic"] == "ESTIMATED"
    assert impact.anchor([info("vol", metric="attempt_volume")]) is None


def test_storage_is_append_only(ch):
    db = "anomalyos_incidents_test"
    for stmt in storage.ddl(db):
        ch.client.command(stmt)
    e = Engine()
    digest = replace(info("d1", locus=(("customer_country", "FR"),)), route="DIGEST")
    r = e.run([Event(H, "detected", "c1", info=info("c1")), Event(2 * H, "detected", "d1", info=digest)])
    n = storage.append(ch.client, db, "run_test", r)
    assert n == {"incident_events": 1, "incident_links": 1, "digest_items": 1}
    storage.append(ch.client, db, "run_test", r)  # a second write adds rows, never replaces them
    assert int(ch.client.command(f"SELECT count() FROM {db}.incident_events WHERE run_id = 'run_test'")) == 2
    assert ch.client.command(f"SELECT status FROM {db}.incidents_current WHERE run_id = 'run_test'") == "DETECTED"
    ch.client.command(f"DROP DATABASE IF EXISTS {db}")
