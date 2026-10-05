"""Score Stage 5 decisions against ground truth (ADR-039 D-9). Rules fixed at Gate 0, before results.

Input: per decided candidate the full Stage 3 candidate (matching uses Stage 3 rules unchanged), the routes of the
Jev policy and of the no-Jev baseline, and the verified answers; ground-truth records; world bounds.
Output: candidate-level route metrics per system — never "pages" or incident counts (grouping is Stage 6) — plus
classification and calibration of the Jev answers. Only evaluation code reads ground truth (INV-015).

Matching status per candidate, precedence incident > watch > suppress > unmatched; a candidate that only touches a
declared unchanged metric is unmatched. Expected route: incident -> INCIDENT, watch -> DIGEST, suppress -> IGNORE.
Ground-truth ``is_incident`` = ``expected_route != suppress`` (incident and watch records); unmatched -> false.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from pulseos.evaluation.detection import _relation

STATUS_ORDER = ("incident", "watch", "suppress")
EXPECTED = {"incident": "INCIDENT", "watch": "DIGEST", "suppress": "IGNORE"}
DAY = 86_400


def status(candidate: Mapping[str, Any], records: list[Mapping[str, Any]], world_end: int) -> tuple[str, dict | None]:
    hits = [r for r in records if _relation(candidate, r, world_end) == "affected"]
    for route in STATUS_ORDER:
        best = sorted((r for r in hits if r["expected_route"] == route), key=lambda r: (r["start"], r["record_key"]))
        if best:
            return route, best[0]
    return "unmatched", None


def _share(xs: list[bool]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def score_decisions(rows: Iterable[Mapping[str, Any]], records: Iterable[Mapping[str, Any]], world_start: int,
                    world_end: int, warmup_days: int = 3) -> dict[str, Any]:
    """``rows``: dicts with ``candidate`` (full Stage 3 candidate dict), ``routes`` ({system: route}), ``verified``,
    ``reasons``, ``answers`` (verified answers by question id or {})."""
    recs = [dict(r) for r in records]
    days = (world_end - world_start) / DAY - warmup_days
    scored = []
    for row in rows:
        st, rec = status(row["candidate"], recs, world_end)
        scored.append({**row, "status": st, "record": rec})
    systems = sorted({s for row in scored for s in row["routes"]})
    incidents = [r for r in recs if r["expected_route"] == "incident" and r.get("oracle_detectable_at") is not None]
    out: dict[str, Any] = {"candidates": len(scored), "days": days, "incidents_scored": len(incidents), "systems": {}}
    for sys in systems:
        routes = Counter(row["routes"][sys] for row in scored)
        by_status = {st: Counter(row["routes"][sys] for row in scored if row["status"] == st)
                     for st in STATUS_ORDER + ("unmatched",)}
        matched = [row for row in scored if row["record"] is not None]
        paged = {}
        for row in scored:
            if row["status"] == "incident" and row["routes"][sys] == "INCIDENT":
                paged.setdefault(row["record"]["record_key"], 0)
                paged[row["record"]["record_key"]] += 1
        out["systems"][sys] = {
            "routes_per_day": {k: routes.get(k, 0) / days for k in ("INCIDENT", "DIGEST", "IGNORE")},
            "routes_by_status": {st: dict(c) for st, c in by_status.items()},
            "route_accuracy_matched": _share([row["routes"][sys] == EXPECTED[row["status"]] for row in matched]),
            "incident_recall_at_incident": _share([r["record_key"] in paged for r in incidents]),
            "incidents_routed_incident": sum(r["record_key"] in paged for r in incidents),
            "incident_routes_per_matched_incident": (sum(paged.values()) / len(paged)) if paged else None,
            "unmatched_incident_per_day": by_status["unmatched"].get("INCIDENT", 0) / days,
            "suppress_incident_per_day": by_status["suppress"].get("INCIDENT", 0) / days,
            "watch_incident_per_day": by_status["watch"].get("INCIDENT", 0) / days,
        }
    verified = [row for row in scored if row["verified"]]
    inc_v = [row for row in verified if row["status"] == "incident"]
    probs = [(row["answers"]["is_incident"]["p"], row["status"] in ("incident", "watch")) for row in verified]
    bins = []
    for lo in range(10):
        b = [(p, y) for p, y in probs if lo / 10 <= p < (lo + 1) / 10 or (lo == 9 and p == 1.0)]
        bins.append({"bin": [lo / 10, (lo + 1) / 10], "n": len(b), "mean_p": (sum(p for p, _ in b) / len(b)) if b else None,
                     "observed": (sum(y for _, y in b) / len(b)) if b else None})

    def top_k(row, k):
        cat = row["answers"]["category"]["probs"]
        return row["record"]["true_cause"] in sorted(cat, key=lambda c: (-cat[c], c))[:k]

    out["answers"] = {
        "verified_share": _share([row["verified"] for row in scored]),
        "failure_reasons": dict(Counter(x for row in scored for x in row["reasons"])),
        "severity_accuracy": _share([max(row["answers"]["severity"]["probs"], key=lambda s: row["answers"]["severity"]["probs"][s])
                                     == row["record"]["severity"] for row in inc_v]),
        "category_top1": _share([top_k(row, 1) for row in inc_v]),
        "category_top3": _share([top_k(row, 3) for row in inc_v]),
        "brier": (sum((p - y) ** 2 for p, y in probs) / len(probs)) if probs else None,
        "reliability": bins,
    }
    out["rows"] = [{"candidate_id": row["candidate"]["anomaly_id"], "status": row["status"],
                    "record_key": row["record"]["record_key"] if row["record"] else None,
                    "scenario_kind": row["record"]["scenario_kind"] if row["record"] else None,
                    "routes": row["routes"], "verified": row["verified"]} for row in scored]
    return out

