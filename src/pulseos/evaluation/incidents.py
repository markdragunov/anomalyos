"""Score the incident engine against ground truth (ADR-041 D-9). Rules fixed at Gate 0, before results.

Input: the engine result (incidents, events, digest groups), the full Stage 3 candidates, ground-truth records, world
bounds. Each linked candidate keeps its Stage 3 matching status and record (``evaluation.decisions.status``).
An engine incident's main record = the incident record matched by most of its candidates (ties: earliest start); if
none, the most frequent matched record of any route; else none (unmatched).
Output: coverage, duplicates, purity, wrong merges (campaign + outage pairs separately), volume, timeliness, recovery,
impact error and interval coverage. Only evaluation code reads ground truth (INV-015).
"""

from __future__ import annotations

import json
from collections import Counter
from statistics import median
from typing import Any, Iterable, Mapping

from pulseos.evaluation.cohorts import INJECTED_TRAFFIC_KINDS, NEW_COHORT_KINDS
from pulseos.evaluation.decisions import status

DAY = 86_400
RECOVERY_TOLERANCE_S = 6 * 3600
CAMPAIGN_KIND = "correlated_unrelated_anomalies"


def _main(records: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for pool in ([r for r in records if r["expected_route"] == "incident"], records):
        if pool:
            counts = Counter(r["record_key"] for r in pool)
            first = {r["record_key"]: r for r in sorted(pool, key=lambda r: (r["start"], r["record_key"]))}
            key = max(counts, key=lambda k: (counts[k], -first[k]["start"], k))
            return first[key]
    return None


def _p(xs: list[float], q: float) -> float | None:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def score_incidents(incidents: Mapping[str, Mapping[str, Any]], incident_events: Iterable[Mapping[str, Any]],
                    digest_groups: Mapping[str, list[str]], candidates: Iterable[Mapping[str, Any]],
                    records: Iterable[Mapping[str, Any]], world_start: int, world_end: int,
                    warmup_days: int = 3) -> dict[str, Any]:
    recs = [dict(r) for r in records]
    by_key = {r["record_key"]: r for r in recs}
    by_incident_id = {r["incident_id"]: r["record_key"] for r in recs if r.get("incident_id")}
    cands = {c["anomaly_id"]: dict(c) for c in candidates}
    days = (world_end - world_start) / DAY - warmup_days
    matched: dict[str, Mapping[str, Any] | None] = {}
    for inc in incidents.values():
        for cid in inc["linked_anomalies"]:
            if cid not in matched:
                matched[cid] = status(cands[cid], recs, world_end)[1]
    events = [dict(e) for e in incident_events]
    first_recovering = {}
    for e in sorted(events, key=lambda e: (e["event_time"], e["incident_id"], e["seq"])):
        if e["status"] == "RECOVERING" and e["incident_id"] not in first_recovering:
            first_recovering[e["incident_id"]] = e["event_time"]
    terminal_by_system = sum(1 for e in events if e["status"] in ("RESOLVED", "DISMISSED") and e["actor"] != "human")

    rows, main_of = [], {}
    for iid, inc in sorted(incidents.items()):
        members = [matched[c] for c in inc["linked_anomalies"]]
        main = _main([m for m in members if m])
        main_of[iid] = main
        keys = {m["record_key"] for m in members if m}
        unrelated = {(a, b) for a in keys for b in keys if a < b and (
            by_key[b].get("incident_id") in (by_key[a].get("unrelated_to") or []) or
            by_key[a].get("incident_id") in (by_key[b].get("unrelated_to") or []))}
        campaign = {(a, b) for a, b in unrelated if CAMPAIGN_KIND in (by_key[a]["scenario_kind"], by_key[b]["scenario_kind"])}
        rows.append({"incident_id": iid, "members": len(members), "main": main["record_key"] if main else None,
                     "main_route": main["expected_route"] if main else "unmatched",
                     "purity": (sum(1 for m in members if m and main and m["record_key"] == main["record_key"]) / len(members)),
                     "wrong_merge": bool(unrelated), "campaign_merge": bool(campaign), "detected_at": inc["detected_at"],
                     "impact": inc.get("estimated_impact")})

    incident_recs = [r for r in recs if r["expected_route"] == "incident" and r.get("oracle_detectable_at") is not None]
    covered = [r for r in incident_recs if any(m and m["record_key"] == r["record_key"] for m in matched.values())]
    mains = Counter(x["main"] for x in rows if x["main"])
    duplicates = [max(0, mains.get(r["record_key"], 0) - 1) for r in covered]
    delays, recovery_hits, impact_err, impact_cov, revenue_err, revenue_cov = [], [], [], [], [], []
    for r in covered:
        created = [x["detected_at"] for x in rows if x["incident_id"] in {
            iid for iid, inc in incidents.items()
            if any(matched[c] and matched[c]["record_key"] == r["record_key"] for c in inc["linked_anomalies"])}]
        if created:
            delays.append((min(created) - max(r["start"], r["oracle_detectable_at"])) / 3600)
        own = [x for x in rows if x["main"] == r["record_key"]]
        true_rec = r.get("expected_recovery") or r.get("end")
        if own and true_rec:
            t = min((first_recovering[x["incident_id"]] for x in own if x["incident_id"] in first_recovering), default=None)
            recovery_hits.append(t is not None and abs(t - true_rec) <= RECOVERY_TOLERANCE_S)
        imp = next((x["impact"] for x in sorted(own, key=lambda x: -x["members"]) if x["impact"]), None)
        if imp:
            lost = (imp.get("lost_successful_payments") or {})
            true_lost = (r.get("expected_impact") or {}).get("lost_successful_payments")
            if lost and true_lost:
                impact_err.append(abs(lost["value"] - true_lost) / true_lost)
                impact_cov.append(lost["interval"][0] <= true_lost <= lost["interval"][1])
            true_rev = (r.get("true_impact") or {}).get("lost_revenue_minor") or {}
            for cur, v in (imp.get("lost_revenue_minor") or {}).get("per_currency", {}).items():
                tv = true_rev.get(cur)
                if tv:
                    revenue_err.append(abs(v["value"] - tv) / tv)
                    revenue_cov.append(v["interval"][0] <= tv <= v["interval"][1])

    def share(xs):
        return sum(xs) / len(xs) if xs else None

    n = len(rows)
    return {
        "summary": {
            "incidents": n, "incidents_per_day": n / days,
            "false_incidents_per_day": sum(1 for x in rows if x["main_route"] in ("unmatched", "suppress")) / days,
            "watch_incidents_per_day": sum(1 for x in rows if x["main_route"] == "watch") / days,
            "digest_groups_per_day": len(digest_groups) / days,
            "incident_records_scored": len(incident_recs), "coverage": share([True] * len(covered) + [False] * (len(incident_recs) - len(covered))),
            "duplicates_per_covered": share(duplicates), "purity": share([x["purity"] for x in rows]),
            "wrong_merge_rate": share([x["wrong_merge"] for x in rows]), "campaign_merges": sum(x["campaign_merge"] for x in rows),
            "creation_delay_h_median": median(delays) if delays else None, "creation_delay_h_p90": _p(delays, 0.9),
            "recovery_within_6h": share(recovery_hits), "terminal_by_system": terminal_by_system,
            "lost_payments_median_rel_error": median(impact_err) if impact_err else None,
            "lost_payments_interval_coverage": share(impact_cov),
            "lost_revenue_median_rel_error": median(revenue_err) if revenue_err else None,
            "lost_revenue_interval_coverage": share(revenue_cov),
        },
        "counts": {"covered": len(covered), "duplicates": sum(duplicates), "wrong_merges": sum(x["wrong_merge"] for x in rows),
                   "recovery_scored": len(recovery_hits), "impact_scored": len(impact_err), "revenue_scored": len(revenue_err),
                   "days": days},
        "lists": {"delays_h": delays, "recovery_hits": recovery_hits, "lost_payments_err": impact_err,
                  "lost_payments_cov": impact_cov, "lost_revenue_err": revenue_err, "lost_revenue_cov": revenue_cov,
                  "purity": [x["purity"] for x in rows]},
        "incidents": rows,
    }


def label_ok(change_type: str, record: Mapping[str, Any] | None) -> bool | None:
    """Stage 4 rate-vs-mix label expectation for an incident record (ADR-037 C-10 with ADR-038 additions)."""
    if record is None or record["expected_route"] != "incident":
        return None
    if record["scenario_kind"] in INJECTED_TRAFFIC_KINDS:
        return change_type in ("mix_shift", "mixed")
    if record["scenario_kind"] in NEW_COHORT_KINDS:
        return change_type in ("new_cohort", "rate_change")
    return change_type == "rate_change"


def labels_by_checkpoint(decisions: Iterable[Any], candidates: Iterable[Mapping[str, Any]],
                         records: Iterable[Mapping[str, Any]], world_end: int) -> dict[int, dict[str, Any]]:
    """Stage 4 label accuracy at first look (0) and at each later checkpoint (1, 2, 3), on matched incident records."""
    recs = [dict(r) for r in records]
    cands = {c["anomaly_id"]: dict(c) for c in candidates}
    per: dict[str, list[Any]] = {}
    for d in decisions:
        per.setdefault(d.candidate_id, []).append(d)
    out: dict[int, list[bool]] = {}
    for cid, ds in per.items():
        rec = status(cands[cid], recs, world_end)[1]
        for i, d in enumerate(sorted(ds, key=lambda d: d.as_of)):
            ct = json.loads(d.state_json).get("change_type") if d.state_json != "{}" else None
            ok = label_ok(ct, rec) if ct else None
            if ok is not None:
                out.setdefault(i, []).append(ok)
    return {i: {"n": len(xs), "accuracy": sum(xs) / len(xs)} for i, xs in sorted(out.items())}
