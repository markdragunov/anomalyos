#!/usr/bin/env python3
"""Stage 4 Phase 2: cohort analysis on DEV seeds (ADR-037 C-10). Writes reports/stage4_dev.{json,md}.

Per world: generate (randomized calendar, scale 1.0), load, normalize, Stage 3 detection, daily sweep, cohort
analysis; scores Stage 3 recall / false positives with and without the sweep, and Stage 4 localization, labels,
impact and cost against a relative-drop baseline. Refuses HELDOUT seeds.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_detection_dev import _Chdb  # noqa: E402
from anomalyos.cohorts import analyze_candidates, sweep  # noqa: E402
from anomalyos.detection import detect  # noqa: E402
from anomalyos.evaluation.cohorts import score_cohorts  # noqa: E402
from anomalyos.evaluation.detection import score  # noqa: E402
from anomalyos.events import store  # noqa: E402
from anomalyos.simulation import clickhouse_load as chl  # noqa: E402
from anomalyos.simulation.runner import generate  # noqa: E402
from anomalyos.simulation.seeds import DEV_SEEDS, HELDOUT_SEEDS  # noqa: E402
from anomalyos.simulation.world import WorldConfig  # noqa: E402


def _slim(a) -> dict:
    d = dataclasses.asdict(a)
    d["top"] = [{"dims": t["dims"], "z": t["z"], "q": t["q"], "coverage": t["coverage"],
                 "counts": [t["row"][k] for k in ("k_b", "n_b", "k_d", "n_d")]} for t in d["top"]]
    d["controls"] = [{"dims": t["dims"], "z": t["z"]} for t in d["controls"]]
    return d


def one_world(args) -> dict:
    seed, realism, scale = args
    work = Path(tempfile.mkdtemp(prefix=f"s4dev_{seed}_{realism}_"))
    try:
        w = WorldConfig(seed=seed, scale=scale, schedule="randomized", realism=realism)
        res = generate(w, "full", work / "run")
        client = _Chdb(str(work / "ch"))
        try:
            chl.load_run(client, work / "run", "anomalyos")
            store.load_norm(client, work / "run", "anomalyos")
            S, E = datetime.fromtimestamp(w.start, timezone.utc), datetime.fromtimestamp(w.end, timezone.utc)
            records = [g.to_dict() for g in res.ground_truth]
            cands = detect(client, "anomalyos", res.run_id, S, E)
            t0 = time.time()
            swept = sweep(client, "anomalyos", res.run_id, S, E)
            t_sweep = time.time() - t0
            t0 = time.time()
            results = analyze_candidates(client, "anomalyos", res.run_id, cands + swept, w.start)
            t_an = time.time() - t0
        finally:
            client.close()
        all_c = [dataclasses.asdict(c) for c in cands + swept]
        analyses = [_slim(a) for a, _ in results]
        return {
            "seed": seed, "realism": realism, "scale": scale,
            "stage3_without_sweep": score(cands, records, w.start, w.end)["summary"],
            "stage3_with_sweep": score(cands + swept, records, w.start, w.end)["summary"],
            "sweep_candidates": len(swept),
            "sweep_fp": sum(1 for c in score(swept, records, w.start, w.end)["candidates"] if c["class"].startswith("fp")),
            "stage4": score_cohorts(analyses, all_c, records, w.end),
            "cost": {"analyses": len(results), "queries": sum(a.queries for a, _ in results), "seconds_analysis": round(t_an, 1),
                     "seconds_sweep": round(t_sweep, 1), "max_bundle_bytes": max((len(json.dumps(b)) for _, b in results), default=0)},
            "raw": {"analyses": analyses, "candidates": all_c, "records": records, "world": [w.start, w.end]},
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _f(x, pct=False):
    return "—" if x is None else (f"{100 * x:.0f} %" if pct else f"{x:.2f}")


def report(results: list[dict]) -> str:
    lines = ["# Stage 4 — cohort intelligence on DEV seeds", "",
             f"Seeds {sorted({r['seed'] for r in results})}; scale {results[0]['scale']}; randomized calendar (report only).", ""]
    lines += ["## Stage 3 with and without the sweep", "", "| realism | recall without | recall with | FP/day without | FP/day with | sweep candidates | sweep FP |",
              "|---|---|---|---|---|---|---|"]
    for realism in sorted({r["realism"] for r in results}):
        rs = [r for r in results if r["realism"] == realism]
        def agg(key, field):
            if field == "recall":
                return sum(r[key]["detected"] for r in rs) / sum(r[key]["incidents_scored"] for r in rs)
            return sum(r[key]["false_positives"] for r in rs) / (25 * len(rs))
        lines.append(f"| {realism} | {_f(agg('stage3_without_sweep', 'recall'))} | {_f(agg('stage3_with_sweep', 'recall'))} | "
                     f"{_f(agg('stage3_without_sweep', 'fp'))} | {_f(agg('stage3_with_sweep', 'fp'))} | "
                     f"{sum(r['sweep_candidates'] for r in rs)} | {sum(r['sweep_fp'] for r in rs)} ({sum(r['sweep_fp'] for r in rs) / (25 * len(rs)):.3f}/day) |")
    lines += ["", "## Stage 4 (first matching candidate per incident)", "",
              "| realism | incidents | top-1 exact | over-specific | coarse | wrong | top-3 exact | any candidate exact | naive top-1 exact | label (incidents) | label (mix records) | impact median rel. error | impact interval coverage |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for realism in sorted({r["realism"] for r in results}):
        rows = [x for r in results if r["realism"] == realism for x in r["stage4"]["records"]]
        inc = [x for x in rows if x["route"] == "incident"]
        def share(xs, k, v=True):
            xs = [x for x in xs if x[k] is not None]
            return sum(x[k] == v for x in xs) / len(xs) if xs else None
        errs = sorted(x["impact_rel_error"] for x in inc if x["impact_rel_error"] is not None)
        lines.append(f"| {realism} | {len(inc)} | {_f(share(inc, 'localization', 'exact'), 1)} | {_f(share(inc, 'localization', 'over_specific'), 1)} | "
                     f"{_f(share(inc, 'localization', 'coarse'), 1)} | {_f(share(inc, 'localization', 'wrong'), 1)} | {_f(share(inc, 'top3_exact'), 1)} | "
                     f"{_f(share(inc, 'any_candidate_exact'), 1)} | {_f(share(inc, 'naive_localization', 'exact'), 1)} | {_f(share(inc, 'label_ok'), 1)} | "
                     f"{_f(share([x for x in rows if x['route'] == 'suppress'], 'label_ok'), 1)} | {_f(median(errs) if errs else None)} | {_f(share(inc, 'impact_covered'), 1)} |")
    lines += ["", "## Localization by scenario kind (v1, first matching candidate)", "", "| kind | n | exact | over-specific | coarse | wrong | label ok |", "|---|---|---|---|---|---|---|"]
    kinds: dict[str, list] = {}
    for r in results:
        if r["realism"] == "v1":
            for x in r["stage4"]["records"]:
                kinds.setdefault(x["scenario_kind"] + (" (suppress)" if x["route"] == "suppress" else ""), []).append(x)
    for k, xs in sorted(kinds.items()):
        n = len(xs)
        c = lambda v: sum(x["localization"] == v for x in xs)
        lab = [x for x in xs if x["label_ok"] is not None]
        lines.append(f"| {k} | {n} | {c('exact')} | {c('over_specific')} | {c('coarse')} | {c('wrong')} | "
                     f"{sum(x['label_ok'] for x in lab)}/{len(lab)} |")
    cost = [r["cost"] for r in results]
    lines += ["", "## Cost", "", f"- analyses per world: median {median(c['analyses'] for c in cost)}; queries per analysis: "
              f"{sum(c['queries'] for c in cost) / max(1, sum(c['analyses'] for c in cost)):.1f}; seconds per analysis: "
              f"{sum(c['seconds_analysis'] for c in cost) / max(1, sum(c['analyses'] for c in cost)):.2f}; sweep seconds per world: "
              f"{median(c['seconds_sweep'] for c in cost)}; max bundle {max(c['max_bundle_bytes'] for c in cost)} bytes"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=list(DEV_SEEDS))
    ap.add_argument("--realism", nargs="*", default=["v1", "v2"])
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "stage4_dev")
    a = ap.parse_args(argv)
    if set(a.seeds) & set(HELDOUT_SEEDS):
        print("refusing to evaluate on HELDOUT_SEEDS", file=sys.stderr)
        return 2
    jobs = [(s, r, a.scale) for r in a.realism for s in a.seeds]
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(one_world, jobs):
            results.append(res)
            s = res["stage4"]["summary"]
            print(f"seed {res['seed']:4} {res['realism']}: top1 {s['top1_exact']} naive {s['naive_top1_exact']} "
                  f"sweep {res['sweep_candidates']} ({res['cost']['seconds_analysis']} s analysis)", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(json.dumps(results, indent=1, sort_keys=True, default=list))
    a.out.with_suffix(".md").write_text(report(results))
    print(f"written {a.out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
