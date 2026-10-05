#!/usr/bin/env python3
"""Stage 6 Phase 2 (ADR-041 D-9): incident engine on DEV seeds.

``--tune``: seeds 1-10 only, route-agnostic events; refolds the engine for every gap G in {0, 1, 3, 6} h and
hysteresis H in {0, 1, 3} h and picks (G, H) by the rule fixed at Gate 0 — (1) zero campaign + outage merges,
(2) wrong merges <= 5 % of engine incidents, (3) fewest duplicates per covered record; ties: smaller G, then smaller H.
Writes reports/stage6_tuning.json.
Default: all DEV seeds with the chosen (G, H), route sources ``all`` (route-agnostic) and ``baseline`` (baseline_v2,
the control), impact estimated; tuning seeds 1-10 and validation seeds 11-20 reported separately. Writes
reports/stage6_dev.{json,md}. Jev is blocked (OQ-1): the fake client only feeds the decision records. Refuses
HELDOUT seeds.
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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_detection_dev import _Chdb  # noqa: E402
from pulseos.detection import detect  # noqa: E402
from pulseos.evaluation.incidents import labels_by_checkpoint, score_incidents  # noqa: E402
from pulseos.events import store  # noqa: E402
from pulseos.incidents import pipeline  # noqa: E402
from pulseos.incidents.config import HOUR, IncidentConfig  # noqa: E402
from pulseos.incidents.engine import Engine, Event  # noqa: E402
from pulseos.jev.client import BudgetedClient, JevBudget  # noqa: E402
from pulseos.jev.fake import FakeJevClient  # noqa: E402
from pulseos.simulation import clickhouse_load as chl  # noqa: E402
from pulseos.simulation.runner import generate  # noqa: E402
from pulseos.simulation.seeds import DEV_SEEDS, HELDOUT_SEEDS  # noqa: E402
from pulseos.simulation.world import WorldConfig  # noqa: E402

TUNING, VALIDATION = set(range(1, 11)), set(range(11, 21))
GAPS = (0, 1 * HOUR, 3 * HOUR, 6 * HOUR)
HYSTERESIS = (0, 1 * HOUR, 3 * HOUR)
WRONG_MERGE_MAX = 0.05


def with_hysteresis(events: list[Event], h: int) -> list[Event]:
    rec = [e for e in events if e.kind == "recovered"]
    return [e for e in events if e.kind != "recovery_check"] + [Event(e.time + h, "recovery_check", e.candidate_id) for e in rec]


def one_world(args) -> dict:
    seed, realism, mode, gap, hyst, scale = args
    work = Path(tempfile.mkdtemp(prefix=f"s6dev_{seed}_{realism}_"))
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
            full = [dataclasses.asdict(c) for c in cands]
            cfg = IncidentConfig(gap_s=gap, hysteresis_s=hyst)
            out = {"seed": seed, "realism": realism}
            sources = ("all",) if mode == "tune" else ("all", "baseline")
            for src in sources:
                jev = BudgetedClient(FakeJevClient(), JevBudget(max_calls=10**6))
                t0 = time.time()
                pr = pipeline.run(client, "anomalyos", res.run_id, cands, w.start, w.end, jev, "fake-jev-0", src, cfg,
                                  estimate_impact=(mode != "tune"))
                seconds = round(time.time() - t0, 1)
                if mode == "tune":
                    grid = {}
                    for g in GAPS:
                        for h in HYSTERESIS:
                            r = Engine(IncidentConfig(gap_s=g, hysteresis_s=h)).run(with_hysteresis(pr.events, h))
                            sc = score_incidents(r.incidents, r.incident_events, r.digest_groups, full, records, w.start, w.end)
                            grid[f"{g}|{h}"] = {"counts": sc["counts"], "summary": sc["summary"]}
                    out["grid"] = grid
                else:
                    e = pr.engine
                    sc = score_incidents(e.incidents, e.incident_events, e.digest_groups, full, records, w.start, w.end)
                    out[src] = {"summary": sc["summary"], "counts": sc["counts"], "lists": sc["lists"], "seconds": seconds,
                                "decisions": len(pr.decisions), "decided": len(pr.infos),
                                "rows": len(e.incident_events), "links": len(e.links)}
                    if src == "all":
                        out["labels_by_checkpoint"] = labels_by_checkpoint(pr.decisions, full, records, w.end)
                        out["kinds"] = [{"main": x["main"], "kind": next((r["scenario_kind"] for r in records if r["record_key"] == x["main"]), None),
                                         "members": x["members"], "purity": x["purity"]} for x in sc["incidents"]]
        finally:
            client.close()
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def choose(worlds: list[dict]) -> dict:
    pooled = {}
    for key in worlds[0]["grid"]:
        c = [w["grid"][key] for w in worlds]
        inc = sum(x["summary"]["incidents"] for x in c)
        covered = sum(x["counts"]["covered"] for x in c)
        pooled[key] = {"campaign_merges": sum(x["summary"]["campaign_merges"] for x in c),
                       "wrong_merge_rate": sum(x["counts"]["wrong_merges"] for x in c) / inc if inc else 0.0,
                       "duplicates_per_covered": sum(x["counts"]["duplicates"] for x in c) / covered if covered else 0.0,
                       "incidents": inc, "covered": covered}
    ok = [k for k, v in pooled.items() if v["campaign_merges"] == 0 and v["wrong_merge_rate"] <= WRONG_MERGE_MAX]
    pick = min(ok, key=lambda k: (pooled[k]["duplicates_per_covered"], int(k.split("|")[0]), int(k.split("|")[1]))) if ok else None
    return {"pooled": pooled, "eligible": ok, "chosen": pick}


def _f(x, pct=False):
    return "—" if x is None else (f"{100 * x:.0f} %" if pct else f"{x:.2f}")


def _pool(ws: list[dict], src: str) -> dict:
    s = [w[src] for w in ws]
    L = {k: [v for x in s for v in x["lists"][k]] for k in s[0]["lists"]}
    days = sum(x["counts"]["days"] for x in s)
    scored = sum(x["summary"]["incident_records_scored"] for x in s)
    covered = sum(x["counts"]["covered"] for x in s)
    inc = sum(x["summary"]["incidents"] for x in s)
    share = lambda xs: (sum(xs) / len(xs)) if xs else None
    return {
        "incidents/day": inc / days,
        "false/day": sum(x["summary"]["false_incidents_per_day"] * x["counts"]["days"] for x in s) / days,
        "digest groups/day": sum(x["summary"]["digest_groups_per_day"] * x["counts"]["days"] for x in s) / days,
        "coverage": covered / scored if scored else None,
        "duplicates": sum(x["counts"]["duplicates"] for x in s) / covered if covered else None,
        "purity": share(L["purity"]), "wrong merges": sum(x["counts"]["wrong_merges"] for x in s) / inc if inc else None,
        "campaign merges": sum(x["summary"]["campaign_merges"] for x in s),
        "delay median h": median(L["delays_h"]) if L["delays_h"] else None,
        "recovery ≤ 6 h": share(L["recovery_hits"]), "terminal by system": sum(x["summary"]["terminal_by_system"] for x in s),
        "lost payments err": median(L["lost_payments_err"]) if L["lost_payments_err"] else None,
        "lost payments cov": share(L["lost_payments_cov"]),
        "lost revenue err": median(L["lost_revenue_err"]) if L["lost_revenue_err"] else None,
        "lost revenue cov": share(L["lost_revenue_cov"]),
    }


def report(results: list[dict], tuning: dict) -> str:
    g, h = (int(x) // HOUR for x in tuning["chosen"].split("|")) if tuning.get("chosen") else (None, None)
    lines = ["# Stage 6 — incident engine on DEV seeds", "",
             "Jev evaluation is blocked (OQ-1); routes come from all candidates (route-agnostic) or from `baseline_v2` (the control).",
             f"Seeds {sorted({r['seed'] for r in results})}; randomized calendar; G = {g} h, H = {h} h (tuned on seeds 1–10).", ""]
    cols = ["incidents/day", "false/day", "digest groups/day", "coverage", "duplicates", "purity", "wrong merges",
            "campaign merges", "delay median h", "recovery ≤ 6 h", "terminal by system", "lost payments err",
            "lost payments cov", "lost revenue err", "lost revenue cov"]
    pct = {"coverage", "purity", "wrong merges", "recovery ≤ 6 h", "lost payments cov", "lost revenue cov"}
    lines += ["| source | seeds | realism | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 3)]
    for src in ("all", "baseline"):
        for name, seeds in (("tuning 1–10", TUNING), ("**validation 11–20**", VALIDATION)):
            for realism in sorted({r["realism"] for r in results}):
                ws = [r for r in results if r["seed"] in seeds and r["realism"] == realism]
                if not ws:
                    continue
                m = _pool(ws, src)
                vals = [str(m[c]) if c in ("campaign merges", "terminal by system") else _f(m[c], c in pct) for c in cols]
                lines.append(f"| {src} | {name} | realism_{realism} | " + " | ".join(vals) + " |")
    lines += ["", "## Stage 4 label accuracy by checkpoint (route-agnostic decisions, matched incident records)", "",
              "| realism | first look | checkpoint 1 | checkpoint 2 | checkpoint 3 |", "|---|---|---|---|---|"]
    for realism in sorted({r["realism"] for r in results}):
        acc = {}
        for r in results:
            if r["realism"] == realism:
                for i, v in r["labels_by_checkpoint"].items():
                    a = acc.setdefault(int(i), [0, 0.0])
                    a[0] += v["n"]
                    a[1] += v["accuracy"] * v["n"]
        lines.append(f"| realism_{realism} | " + " | ".join(
            f"{100 * acc[i][1] / acc[i][0]:.0f} % (n={acc[i][0]})" if i in acc else "—" for i in range(4)) + " |")
    lines += ["", "## Tuning grid (seeds 1–10, route-agnostic, pooled)", "", "| G h | H h | campaign merges | wrong merges | duplicates per covered | incidents |",
              "|---|---|---|---|---|---|"]
    for k, v in sorted(tuning["pooled"].items(), key=lambda kv: tuple(int(x) for x in kv[0].split("|"))):
        gg, hh = (int(x) // HOUR for x in k.split("|"))
        mark = " **chosen**" if k == tuning.get("chosen") else ""
        lines.append(f"| {gg} | {hh}{mark} | {v['campaign_merges']} | {_f(v['wrong_merge_rate'], True)} | {_f(v['duplicates_per_covered'])} | {v['incidents']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--seeds", type=int, nargs="*", default=None)
    ap.add_argument("--realism", nargs="*", default=["v1", "v2"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--tuning-file", type=Path, default=ROOT / "reports" / "stage6_tuning.json")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "stage6_dev")
    a = ap.parse_args(argv)
    seeds = a.seeds if a.seeds is not None else (sorted(TUNING) if a.tune else list(DEV_SEEDS))
    if set(seeds) & set(HELDOUT_SEEDS):
        print("refusing to evaluate on HELDOUT_SEEDS", file=sys.stderr)
        return 2
    if a.tune and not set(seeds) <= TUNING:
        print("refusing: tuning uses DEV seeds 1-10 only", file=sys.stderr)
        return 2
    if a.tune:
        jobs = [(s, r, "tune", HOUR, HOUR, a.scale) for r in a.realism for s in seeds]
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            worlds = list(ex.map(one_world, jobs))
        t = choose(worlds)
        a.tuning_file.parent.mkdir(parents=True, exist_ok=True)
        a.tuning_file.write_text(json.dumps(t, indent=1))
        print(json.dumps({"chosen": t["chosen"], "eligible": t["eligible"]}, indent=1))
        return 0
    tuning = json.loads(a.tuning_file.read_text())
    if not tuning.get("chosen"):
        print("no eligible (G, H) in the tuning file", file=sys.stderr)
        return 3
    gap, hyst = (int(x) for x in tuning["chosen"].split("|"))
    jobs = [(s, r, "eval", gap, hyst, a.scale) for r in a.realism for s in seeds]
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(one_world, jobs):
            results.append(res)
            print(f"seed {res['seed']:4} {res['realism']}: {res['all']['summary']['incidents']} incidents (all), "
                  f"{res['baseline']['summary']['incidents']} (baseline), {res['all']['seconds']} s", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(json.dumps(results, indent=1, sort_keys=True, default=list))
    a.out.with_suffix(".md").write_text(report(results, tuning))
    print(f"written {a.out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
