"""Score Stage 4 analyses against ground truth (ADR-037 design C-10). Rules fixed at Gate 0, before results.

Input: analyses (dicts of ``CohortAnalysis``), the Stage 3 candidates they belong to, ground-truth records, world bounds.
Output: per-record localization / label / impact outcomes and a summary. Matching reuses the Stage 3 rules
(``evaluation.detection._relation``). Only evaluation code reads ground truth (INV-015).
"""

from __future__ import annotations

from statistics import median
from typing import Any, Iterable, Mapping

from anomalyos.evaluation.detection import _relation

MIX_KINDS = {"harmless_seasonality", "correlated_unrelated_anomalies", "mix_shift_masking"}
IMPACT_METRICS = {"authorization_rate", "checkout_conversion_rate"}


def localization(locus: Iterable | None, truth: Mapping[str, list[str]]) -> str:
    """exact | over_specific | coarse | wrong, comparing dimensions and values."""
    if locus is None:
        return "wrong"
    ours = {d: v for d, v in locus}
    shared = set(ours) & set(truth)
    if any(ours[d] not in truth[d] for d in shared):
        return "wrong"
    if set(ours) == set(truth):
        return "exact"
    if set(ours) > set(truth):
        return "over_specific"
    if set(ours) < set(truth):
        return "coarse"
    return "wrong"


def score_cohorts(analyses: Iterable[Mapping[str, Any]], candidates: Iterable[Mapping[str, Any]],
                  records: Iterable[Mapping[str, Any]], world_end: int) -> dict[str, Any]:
    by_id = {a["candidate_id"]: a for a in analyses}
    cands = [c for c in candidates if c["status"] != "suppressed" and c["anomaly_id"] in by_id]
    rows = []
    for r in records:
        matched = sorted((c for c in cands if _relation(c, r, world_end) == "affected"), key=lambda c: (c["detected_at"], c["anomaly_id"]))
        if not matched:
            continue
        first = by_id[matched[0]["anomaly_id"]]
        truth = (r.get("root_cause") or {}).get("locus") or {}
        loc = localization(first["locus"], truth)
        naive = localization(first.get("naive_locus"), truth) if first["kind"] == "rate" else None
        top3 = any(localization(t["dims"], truth) == "exact" for t in first["top"][:3])
        any_exact = any(localization(by_id[c["anomaly_id"]]["locus"], truth) == "exact" for c in matched)
        if r["expected_route"] == "suppress" and r["scenario_kind"] in MIX_KINDS:
            want = {"mix_shift", "volume_only"}
        elif r["expected_route"] == "incident" and first["kind"] == "rate":
            want = {"rate_change"}
        else:
            want = None
        label_ok = None if want is None else first["label"] in want
        impact_err = coverage = None
        imp, true_lost = first.get("impact"), (r.get("expected_impact") or {}).get("lost_successful_payments")
        if (r["expected_route"] == "incident" and first["metric"] in IMPACT_METRICS and imp and imp.get("measure") == "lost_successes"
                and true_lost):
            impact_err = abs(imp["value"] - true_lost) / true_lost
            coverage = imp["interval"][0] <= true_lost <= imp["interval"][1]
        rows.append({"record_key": r["record_key"], "scenario_kind": r["scenario_kind"], "route": r["expected_route"],
                     "candidate_id": first["candidate_id"], "metric": first["metric"], "locus": first["locus"],
                     "truth_locus": truth, "localization": loc, "naive_localization": naive, "top3_exact": top3, "any_candidate_exact": any_exact,
                     "label": first["label"], "label_ok": label_ok, "impact_rel_error": impact_err, "impact_covered": coverage,
                     "n_candidates": len(matched)})
    inc = [x for x in rows if x["route"] == "incident"]
    def share(xs, key, val=True):
        xs = [x for x in xs if x[key] is not None]
        return (sum(x[key] == val for x in xs) / len(xs)) if xs else None
    errs = sorted(x["impact_rel_error"] for x in inc if x["impact_rel_error"] is not None)
    return {
        "records": rows,
        "summary": {
            "incidents_localized": len(inc),
            "top1_exact": share(inc, "localization", "exact"),
            "over_specific": share(inc, "localization", "over_specific"),
            "coarse": share(inc, "localization", "coarse"),
            "wrong": share(inc, "localization", "wrong"),
            "top3_exact": share(inc, "top3_exact"),
            "any_candidate_exact": share(inc, "any_candidate_exact"),
            "naive_top1_exact": share(inc, "naive_localization", "exact"),
            "label_accuracy_incidents": share(inc, "label_ok"),
            "label_accuracy_mix_records": share([x for x in rows if x["route"] == "suppress"], "label_ok"),
            "impact_median_rel_error": median(errs) if errs else None,
            "impact_interval_coverage": share(inc, "impact_covered"),
        },
    }
