"""Score detection candidates against ground truth (ADR-034 D-7). Rules were fixed before any result was seen.

Input: candidates (``AnomalyCandidate`` or dicts with the same fields), ground-truth records (dicts as in
``ground_truth.json``), world start/end. Output: per-record outcomes, per-candidate classification, summary.
Rules
* match = time overlap with [start, max(end, expected_recovery)] (open-ended: world end) AND detected_at >= start
  (Gate 1 correction, owner OK: a candidate opened before the record began is not its detection) AND compatible scope
  (each candidate dimension equals the record's affected cohort value or the cohort does not constrain it) AND the
  candidate metric maps to one of the record's ``affected_metrics``;
* recall over ``incident`` records with ``oracle_detectable_at`` set (ADR-031); misses carry a reason;
* latency = detected_at of the first matching candidate - max(start, oracle_detectable_at);
* false positive = promoted candidate matching no incident: matching only ``suppress`` records (by kind), nothing,
  or a metric the record declares unchanged; candidates matching only ``watch`` records are reported, not scored.
Invariants: pure; reads ground truth only here.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from statistics import median
from typing import Any, Iterable, Mapping

WARMUP_S = 3 * 86_400

# candidate metric -> ground-truth metric names it can evidence
METRIC_FAMILIES: dict[str, frozenset[str]] = {
    "authorization_rate": frozenset({"charge_approval_rate", "global_charge_approval_rate", "technical_failure_rate"}),
    "checkout_conversion_rate": frozenset({"payment_intent_conversion_rate"}),
    "renewal_success_rate": frozenset({"renewal_success_rate", "dunning_recovery_rate", "renewal_first_attempt_success_rate"}),
    "dunning_recovery_rate": frozenset({"dunning_recovery_rate", "renewal_success_rate"}),
    "refund_rate": frozenset({"refund_rate"}),
    "duplicate_charge_rate": frozenset({"duplicate_charge_rate"}),
    "attempt_volume": frozenset({"charge_attempt_volume", "checkout_volume", "ingestion_delay"}),
    "fraud_flag_rate": frozenset({"fraud_flag_rate"}),
    "subscription_cancellation_rate": frozenset({"subscription_cancellation_rate"}),
    "late_arrival_share": frozenset({"ingestion_delay"}),
}


def _c(x: Any) -> dict:
    return asdict(x) if is_dataclass(x) else dict(x)


def _interval(rec: Mapping[str, Any], world_end: int) -> tuple[int, int]:
    end = rec["end"] if rec.get("end") is not None else world_end
    return rec["start"], max(end, rec.get("expected_recovery") or 0)


def _scope_ok(scope: Iterable, cohorts: list[Mapping[str, list[str]]]) -> bool:
    scope = [tuple(x) for x in scope]
    if not scope:
        return True
    return any(all(dim not in c or val in c[dim] for dim, val in scope) for c in cohorts)


def _relation(cand: Mapping[str, Any], rec: Mapping[str, Any], world_end: int) -> str | None:
    """'affected' | 'unchanged' | None for one candidate and one record."""
    s, e = _interval(rec, world_end)
    if not (cand["window_start"] < e and s < cand["window_end"]):
        return None
    if cand["detected_at"] < rec["start"]:
        return None  # a change cannot be detected before it begins (Gate 1 fix: open older candidates do not count)
    if not _scope_ok(cand["scope"], rec.get("affected_cohorts") or []):
        return None
    fam = METRIC_FAMILIES.get(cand["metric"], frozenset())
    if fam & set(rec.get("affected_metrics") or []):
        return "affected"
    if fam & set(rec.get("unchanged_metrics") or []):
        return "unchanged"
    return None


def score(candidates: Iterable[Any], records: Iterable[Mapping[str, Any]], world_start: int, world_end: int) -> dict[str, Any]:
    cands = [_c(c) for c in candidates if _c(c)["status"] != "suppressed"]
    recs = [dict(r) for r in records]
    rel = [[_relation(c, r, world_end) for r in recs] for c in cands]

    record_rows = []
    for j, r in enumerate(recs):
        if r["expected_route"] != "incident":
            continue
        hits = [cands[i] for i in range(len(cands)) if rel[i][j] == "affected"]
        anchor = max(r["start"], r.get("oracle_detectable_at") or r["start"])
        first = min((c["detected_at"] for c in hits), default=None)
        if r.get("oracle_detectable_at") is None:
            reason = "oracle_null"
        elif first is not None:
            reason = "detected"
        elif _interval(r, world_end)[1] <= world_start + WARMUP_S:
            reason = "warm_up"
        else:
            reason = "missed"
        record_rows.append({
            "record_key": r["record_key"], "scenario_kind": r["scenario_kind"], "outcome": reason,
            "latency_s": (first - anchor) if first is not None else None, "n_candidates": len(hits),
        })

    cand_rows = []
    for i, c in enumerate(cands):
        kinds = {"affected": [], "unchanged": []}
        for j, r in enumerate(recs):
            if rel[i][j]:
                kinds[rel[i][j]].append(r)
        incident = [r for r in kinds["affected"] if r["expected_route"] == "incident"]
        watch = [r for r in kinds["affected"] if r["expected_route"] == "watch"]
        suppress = [r for r in kinds["affected"] if r["expected_route"] == "suppress"]
        if incident:
            cls, detail = "true_positive", sorted({r["record_key"] for r in incident})
        elif watch:
            cls, detail = "watch", sorted({r["record_key"] for r in watch})
        elif suppress:
            cls, detail = "fp_suppress", sorted({r["scenario_kind"] for r in suppress})
        elif kinds["unchanged"]:
            cls, detail = "fp_unchanged_metric", sorted({r["record_key"] for r in kinds["unchanged"]})
        else:
            cls, detail = "fp_unmatched", []
        cand_rows.append({"anomaly_id": c["anomaly_id"], "series_key": c["series_key"], "metric": c["metric"],
                          "scope": [list(x) for x in c["scope"]], "detected_at": c["detected_at"],
                          "class": cls, "detail": detail})

    scored = [r for r in record_rows if r["outcome"] != "oracle_null"]
    detected = [r for r in scored if r["outcome"] == "detected"]
    lat = sorted(r["latency_s"] for r in detected)
    fps = [c for c in cand_rows if c["class"].startswith("fp_")]
    days = max(1e-9, (world_end - world_start - WARMUP_S) / 86_400)
    return {
        "records": record_rows,
        "candidates": cand_rows,
        "summary": {
            "incidents_scored": len(scored),
            "incidents_oracle_null": sum(r["outcome"] == "oracle_null" for r in record_rows),
            "detected": len(detected),
            "recall": len(detected) / len(scored) if scored else None,
            "missed_warm_up": sum(r["outcome"] == "warm_up" for r in scored),
            "missed": sum(r["outcome"] == "missed" for r in scored),
            "latency_median_s": median(lat) if lat else None,
            "latency_p90_s": lat[min(len(lat) - 1, int(0.9 * len(lat)))] if lat else None,
            "candidates": len(cand_rows),
            "false_positives": len(fps),
            "false_positives_per_day": len(fps) / days,
            "fp_by_class": {k: sum(c["class"] == k for c in cand_rows) for k in ("fp_unmatched", "fp_suppress", "fp_unchanged_metric")},
            "watch_hits": sum(c["class"] == "watch" for c in cand_rows),
        },
    }
