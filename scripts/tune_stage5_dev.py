#!/usr/bin/env python3
"""Stage 5 Gate 1 follow-up (ADR-039, owner OK): tune JevState v2 impact edges and baseline_v2 on DEV seeds 1-10 only.

Rules fixed before results:
1. Impact becomes a rate: estimated lost successes (or excess events) per hour of the first-look window. Bucket edges
   are the 25 / 50 / 75 % quantiles of that rate over all decided candidates of the tuning seeds (no labels used),
   rounded to two significant digits.
2. baseline_v2 keeps the shape of baseline_v1 (INCIDENT if strength >= S, change type in T, impact >= I and locus share
   >= L; IGNORE for weak composition; otherwise DIGEST) and picks S, T, I, L maximizing pooled incident recall at
   INCIDENT subject to (unmatched + suppress) -> INCIDENT <= 0.10 per day; ties: fewer INCIDENT routes per day.
Refuses any seed outside 1-10 (validation seeds 11-20 and HELDOUT are never read here). Writes reports/stage5_tuning.json.
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_detection_dev import _Chdb  # noqa: E402
from pulseos.detection import detect  # noqa: E402
from pulseos.evaluation.decisions import status  # noqa: E402
from pulseos.events import store  # noqa: E402
from pulseos.jev.client import BudgetedClient, JevBudget  # noqa: E402
from pulseos.jev.fake import FakeJevClient  # noqa: E402
from pulseos.policy import mode_a  # noqa: E402
from pulseos.simulation import clickhouse_load as chl  # noqa: E402
from pulseos.simulation.runner import generate  # noqa: E402
from pulseos.simulation.world import WorldConfig  # noqa: E402

TUNING = tuple(range(1, 11))
NON_INCIDENT_BUDGET = 0.10
STRENGTH = ("weak", "moderate", "strong")
SHARE = ("not_provided", "minor", "partial", "most", "nearly_all")
IMPACT = ("negligible", "small", "medium", "large")


def one_world(args) -> dict:
    seed, realism = args
    work = Path(tempfile.mkdtemp(prefix=f"s5tune_{seed}_{realism}_"))
    try:
        w = WorldConfig(seed=seed, scale=1.0, schedule="randomized", realism=realism)
        res = generate(w, "full", work / "run")
        c = _Chdb(str(work / "ch"))
        try:
            chl.load_run(c, work / "run", "anomalyos")
            store.load_norm(c, work / "run", "anomalyos")
            S, E = datetime.fromtimestamp(w.start, timezone.utc), datetime.fromtimestamp(w.end, timezone.utc)
            cands = detect(c, "anomalyos", res.run_id, S, E)
            out = mode_a.run(c, "anomalyos", res.run_id, cands, w.start, BudgetedClient(FakeJevClient(), JevBudget(10**6)), "x")
        finally:
            c.close()
        recs = [g.to_dict() for g in res.ground_truth]
        full = {x.anomaly_id: dataclasses.asdict(x) for x in cands}
        rows = []
        for d in out:
            st, rec = status(full[d.record.candidate_id], recs, w.end)
            state = json.loads(d.record.state_json) if d.record.state_json != "{}" else None
            imp = (d.bundle or {}).get("impact") if d.bundle else None
            hours = max(1e-9, (d.candidate.window_end - d.candidate.window_start) / 3600)
            rows.append({"status": st, "record_key": rec["record_key"] if rec else None, "state": state,
                         "impact_rate": (max(0.0, imp["value"]) / hours) if imp else None})
        return {"seed": seed, "realism": realism, "days": (w.end - w.start) / 86400 - 3, "rows": rows,
                "incidents": [r["record_key"] for r in recs
                              if r["expected_route"] == "incident" and r.get("oracle_detectable_at") is not None]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _sig2(x: float) -> float:
    return float(f"{x:.2g}")


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def impact_bucket(rate: float | None, edges: tuple[float, float, float]) -> str:
    if rate is None:
        return "not_provided"
    return IMPACT[sum(rate >= e for e in edges)]


def evaluate(worlds, edges, s_min, types, i_min, l_min) -> dict:
    routed, scored, non_inc, total, days = set(), 0, 0, 0, 0.0
    for w in worlds:
        days += w["days"]
        scored += len(w["incidents"])
        for r in w["rows"]:
            st = r["state"]
            if st is None:
                continue
            ok = (STRENGTH.index(st["strength"]) >= STRENGTH.index(s_min) if st["strength"] in STRENGTH else False) \
                and st["change_type"] in types \
                and (impact_bucket(r["impact_rate"], edges) in IMPACT[IMPACT.index(i_min):]) \
                and SHARE.index(st["locus_share"]) >= SHARE.index(l_min)
            if ok:
                total += 1
                if r["status"] == "incident":
                    routed.add((w["seed"], w["realism"], r["record_key"]))
                elif r["status"] in ("unmatched", "suppress"):
                    non_inc += 1
    incidents = {(w["seed"], w["realism"], k) for w in worlds for k in w["incidents"]}
    return {"recall": len(routed & incidents) / scored if scored else 0.0, "non_incident_per_day": non_inc / days,
            "incident_per_day": total / days}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=list(TUNING))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "stage5_tuning.json")
    a = ap.parse_args(argv)
    if not set(a.seeds) <= set(TUNING):
        print("refusing: tuning uses DEV seeds 1-10 only", file=sys.stderr)
        return 2
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        worlds = list(ex.map(one_world, [(s, r) for r in ("v1", "v2") for s in a.seeds]))
    rates = [r["impact_rate"] for w in worlds for r in w["rows"] if r["impact_rate"] is not None]
    edges = tuple(_sig2(_quantile(rates, q)) for q in (0.25, 0.5, 0.75))
    type_sets = {"rate_like": ("rate_change", "new_cohort", "mixed"),
                 "not_composition": ("rate_change", "new_cohort", "mixed", "insufficient_data")}
    best = None
    for s_min, (tname, types), i_min, l_min in itertools.product(STRENGTH, type_sets.items(), IMPACT, ("not_provided", "partial")):
        m = evaluate(worlds, edges, s_min, types, i_min, l_min)
        if m["non_incident_per_day"] > NON_INCIDENT_BUDGET:
            continue
        key = (m["recall"], -m["incident_per_day"])
        if best is None or key > best[0]:
            best = (key, {"strength_min": s_min, "change_types": tname, "impact_min": i_min, "locus_share_min": l_min, **m})
    result = {"seeds": a.seeds, "impact_rate_edges_per_hour": edges, "baseline_v2": best[1] if best else None}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
