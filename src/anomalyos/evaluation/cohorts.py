"""Score Stage 4 analyses against ground truth (ADR-037 design C-10; Gate 1 additions ADR-038).

The literal C-10 comparison (dimensions and values against ``root_cause.locus``) stays the headline. ADR-038 adds a
second, equivalence-aware number reported beside it: structurally equivalent dimensions of the synthetic world are
normalized, ``channel=renewal`` is implied by the renewal metrics, and any ``affected_cohorts`` entry also counts.

Input: analyses (dicts of ``CohortAnalysis``), the Stage 3 candidates they belong to, ground-truth records, world bounds.
Output: per-record localization / label / impact outcomes and a summary. Matching reuses the Stage 3 rules
(``evaluation.detection._relation``). Only evaluation code reads ground truth (INV-015).
"""

from __future__ import annotations

from statistics import median
from typing import Any, Iterable, Mapping

from anomalyos.evaluation.detection import _relation
from anomalyos.simulation.world import CARD_BRANDS, LOCAL_METHOD_PSP, PSPS

MIX_KINDS = {"harmless_seasonality", "correlated_unrelated_anomalies", "mix_shift_masking"}
INJECTED_TRAFFIC_KINDS = {"fraud_like_spike"}  # added attempts change the mix: composition is the right label (ADR-038)
NEW_COHORT_KINDS = {"checkout_regression_app_version"}
IMPACT_METRICS = {"authorization_rate", "checkout_conversion_rate"}
RENEWAL_METRICS = {"renewal_success_rate", "dunning_recovery_rate"}
CARD_ONLY_PSPS = set(PSPS) - set(LOCAL_METHOD_PSP.values())


def _canonical(d: Mapping[str, Any], metric: str) -> dict[str, list[str]]:
    """Normalize dimension values that the synthetic world makes equivalent (evaluation only)."""
    d = {k: list(v) if isinstance(v, (list, tuple)) else [v] for k, v in d.items()}
    if d.get("app_version") == ["web"]:
        d.pop("app_version")
        d["platform"] = ["web"]
    method = (d.get("payment_method_type") or [None])[0]
    if d.get("card_brand") == ["unknown"] and method not in (None, "card"):
        d.pop("card_brand")
    if method == "card" and ((d.get("psp") or [None])[0] in CARD_ONLY_PSPS or set(d.get("card_brand", ())) & set(CARD_BRANDS)):
        d.pop("payment_method_type")
    if d.get("psp") == ["unknown"]:
        d.pop("psp")
    if method in LOCAL_METHOD_PSP and d.get("psp") == [LOCAL_METHOD_PSP[method]]:
        d.pop("psp")
    if metric in RENEWAL_METRICS and d.get("channel") == ["renewal"]:
        d.pop("channel")
    return d


def equivalent_exact(locus: Iterable | None, record: Mapping[str, Any], metric: str) -> bool:
    """Exact after normalization, against the root-cause locus or any affected cohort."""
    if locus is None:
        return False
    ours = [(k, v[0]) for k, v in _canonical(dict(locus), metric).items()]
    truths = [(record.get("root_cause") or {}).get("locus") or {}] + list(record.get("affected_cohorts") or [])
    return any(localization(ours, _canonical(t, metric)) == "exact" for t in truths)


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
        # the locus is what is presented first; then the ranked cohorts (ADR-038: top-3 includes the locus)
        top3 = loc == "exact" or any(localization(t["dims"], truth) == "exact" for t in first["top"][:3])
        any_exact = any(localization(by_id[c["anomaly_id"]]["locus"], truth) == "exact" for c in matched)
        if r["expected_route"] == "suppress" and r["scenario_kind"] in MIX_KINDS:
            want = {"mix_shift", "volume_only"}
        elif r["expected_route"] == "incident" and r["scenario_kind"] in INJECTED_TRAFFIC_KINDS and first["kind"] == "rate":
            want = {"mix_shift", "mixed"}
        elif r["expected_route"] == "incident" and r["scenario_kind"] in NEW_COHORT_KINDS and first["kind"] == "rate":
            want = {"new_cohort", "rate_change"}
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
                     "truth_locus": truth, "localization": loc,
                     "equivalent_exact": equivalent_exact(first["locus"], r, first["metric"]), "naive_localization": naive, "top3_exact": top3, "any_candidate_exact": any_exact,
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
            "top1_equivalent_exact": share(inc, "equivalent_exact"),
            "top3_exact": share(inc, "top3_exact"),
            "any_candidate_exact": share(inc, "any_candidate_exact"),
            "naive_top1_exact": share(inc, "naive_localization", "exact"),
            "label_accuracy_incidents": share(inc, "label_ok"),
            "label_accuracy_mix_records": share([x for x in rows if x["route"] == "suppress"], "label_ok"),
            "impact_median_rel_error": median(errs) if errs else None,
            "impact_interval_coverage": share(inc, "impact_covered"),
        },
    }
