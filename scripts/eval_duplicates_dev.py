"""Duplicate-incident task, Phase 3 (ADR-052 D-5): options A, B, C on DEV seeds. Evaluation only.

Per world: one route-agnostic Stage 6 pipeline run with parent loci computed, then engine refolds for every
combination of option A (parent locus), B (nesting mode) and C (renewal with approval). The selection rule was fixed
at Gate 1, before results: on tuning seeds 1-10, an option is accepted if duplicates per covered record fall below the
baseline, the wrong-merge rate rises by at most 1 point, campaign + outage merges stay 0 and coverage falls by at most
1 point; the B variant with fewer duplicates wins (B-chains within 0.02); accepted options are combined, dropping the
smallest gain until the combination passes. ``--validate`` scores seeds 11-20 for the baseline and the chosen
combination, with the Gate 0 duplicate classifier. Writes ``reports/dup_{tuning,validation}.{json,md}``; refuses HELDOUT.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_detection_dev import _Chdb  # noqa: E402
from pulseos.detection import detect  # noqa: E402
from pulseos.evaluation import duplicates as dup  # noqa: E402
from pulseos.evaluation.cohorts import _canonical, localization  # noqa: E402
from pulseos.evaluation.decisions import status  # noqa: E402
from pulseos.evaluation.incidents import score_incidents  # noqa: E402
from pulseos.events import store  # noqa: E402
from pulseos.incidents import pipeline  # noqa: E402
from pulseos.incidents.config import IncidentConfig  # noqa: E402
from pulseos.jev.client import BudgetedClient, JevBudget  # noqa: E402
from pulseos.jev.fake import FakeJevClient  # noqa: E402
from pulseos.simulation import clickhouse_load as chl  # noqa: E402
from pulseos.simulation.runner import generate  # noqa: E402
from pulseos.simulation.seeds import HELDOUT_SEEDS  # noqa: E402
from pulseos.simulation.world import WorldConfig  # noqa: E402

TUNING, VALIDATION = list(range(1, 11)), list(range(11, 21))
WRONG_MERGE_MAX_UP, COVERAGE_MAX_DOWN, B_TIE = 0.01, 0.01, 0.02


def key(a: bool, b: str, c: bool, i: bool = False) -> str:
    """``i``: the ADR-053 rule (an ingestion-only member anchors only ingestion candidates)."""
    return f"A{int(a)}|{b}|C{int(c)}" + ("|I1" if i else "")


VARIANTS = [key(a, b, c) for a, b, c in product((False, True), ("chains", "chains_plus", "pairs"), (False, True))] + [
    key(False, "chains_plus", False, True), key(False, "pairs", False, True)]
BASELINE = key(False, "chains", False)
# ADR-053 protocol: baseline = the incidents_v4 default; candidates with the rule; pairs alone for reference
ING_BASELINE = key(False, "chains_plus", False)
ING_CANDIDATES = {"chains_plus+rule": key(False, "chains_plus", False, True), "pairs+rule": key(False, "pairs", False, True)}
ING_REFERENCE = key(False, "pairs", False)


def cfg_of(k: str) -> IncidentConfig:
    a, b, c, *i = k.split("|")
    return IncidentConfig(parent_locus=a == "A1", nesting=b, renewal_with_approval=c == "C1",
                          ingestion_anchors_others=not i)


def one_world(args) -> dict:
    seed, realism, scale, classify_keys, with_parent = args
    work = Path(tempfile.mkdtemp(prefix=f"dup_{seed}_{realism}_"))
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
            parent_seconds = [0.0]
            real_parent = pipeline.parent_locus

            def timed_parent(*a, **kw):
                t0 = time.perf_counter()
                try:
                    return real_parent(*a, **kw)
                finally:
                    parent_seconds[0] += time.perf_counter() - t0

            pipeline.parent_locus = timed_parent
            t0 = time.perf_counter()
            jev = BudgetedClient(FakeJevClient(), JevBudget(max_calls=10**6))
            pr = pipeline.run(client, "anomalyos", res.run_id, cands, w.start, w.end, jev, "fake-jev-0", "all",
                              IncidentConfig(), estimate_impact=False, parent_loci=with_parent)
            seconds = time.perf_counter() - t0
            pipeline.parent_locus = real_parent
            out = {"seed": seed, "realism": realism, "seconds": round(seconds, 1),
                   "parent_seconds": round(parent_seconds[0], 1), "variants": {}, "classified": {},
                   "parents": parent_accuracy(pr.events, full, records, w.end)}
            for k in VARIANTS:
                cfg = cfg_of(k)
                eng = dup.RecordingEngine(cfg)
                r = eng.run(pr.events)
                sc = score_incidents(r.incidents, r.incident_events, r.digest_groups, full, records, w.start, w.end)
                out["variants"][k] = {"counts": sc["counts"], "incidents": sc["summary"]["incidents"],
                                      "scored": sc["summary"]["incident_records_scored"],
                                      "campaign_merges": sc["summary"]["campaign_merges"],
                                      "purity_sum": sum(sc["lists"]["purity"]),
                                      "dup_by_kind": dup_by_kind(sc["incidents"], records),
                                      "ingestion_anchored": ingestion_anchored(eng, sc["incidents"])}
                if k in classify_keys:
                    out["classified"][k] = [d for d in dup.classify(eng, sc["incidents"], records, cfg) if d["counted"]]
        finally:
            client.close()
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def dup_by_kind(rows, records) -> dict:
    kind = {r["record_key"]: r["scenario_kind"] for r in records if r["expected_route"] == "incident"
            and r.get("oracle_detectable_at") is not None}
    mains = Counter(x["main"] for x in rows if x["main"] in kind)
    out = Counter()
    for k, n in mains.items():
        out[kind[k]] += n - 1
    return dict(out)


def ingestion_anchored(eng, rows) -> dict:
    """Joins whose anchor is an ingestion-only member and whose candidate is not, and the wrong merges among them."""
    hub = {}
    for link in eng.links:
        if link["kind"] not in ("join", "support"):
            continue
        anchor = json.loads(link["evidence_json"]).get("member")
        a, c = eng.info.get(anchor), eng.info.get(link["candidate_id"])
        if a and c and a.metric == "late_arrival_share" and c.metric != "late_arrival_share":
            hub.setdefault(link["incident_id"], 0)
            hub[link["incident_id"]] += 1
    wrong = {x["incident_id"] for x in rows if x["wrong_merge"]}
    campaign = {x["incident_id"] for x in rows if x["campaign_merge"]}
    return {"joins": sum(hub.values()), "incidents": len(hub), "wrong_merge_incidents": len(set(hub) & wrong),
            "campaign_merge_incidents": len(set(hub) & campaign)}


def parent_accuracy(events, full, records, world_end) -> dict:
    """Option A diagnostic: candidates with a parent locus, and how the parent compares with the root cause."""
    cands = {c["anomaly_id"]: c for c in full}
    seen, out = set(), Counter()
    for ev in events:
        if ev.kind != "detected" or ev.candidate_id in seen:
            continue
        seen.add(ev.candidate_id)
        out["candidates"] += 1
        if not ev.info.parent_locus:
            continue
        out["with_parent"] += 1
        rec = status(cands[ev.candidate_id], records, world_end)[1]
        if rec is None or rec["expected_route"] != "incident":
            out["parent_no_incident_record"] += 1
            continue
        truth = _canonical((rec.get("root_cause") or {}).get("locus") or {}, ev.info.metric)
        ours = _canonical(dict(ev.info.parent_locus), ev.info.metric)
        out["parent_" + localization([(d, v[0]) for d, v in ours.items()], truth)] += 1
    return dict(out)


def pooled(worlds: list[dict], k: str) -> dict:
    v = [w["variants"][k] for w in worlds]
    inc = sum(x["incidents"] for x in v)
    covered = sum(x["counts"]["covered"] for x in v)
    scored = sum(x["scored"] for x in v)
    return {"duplicates_per_covered": sum(x["counts"]["duplicates"] for x in v) / covered if covered else 0.0,
            "wrong_merge_rate": sum(x["counts"]["wrong_merges"] for x in v) / inc if inc else 0.0,
            "campaign_merges": sum(x["campaign_merges"] for x in v), "coverage": covered / scored if scored else 0.0,
            "purity": sum(x["purity_sum"] for x in v) / inc if inc else 0.0, "incidents": inc,
            "duplicates": sum(x["counts"]["duplicates"] for x in v), "covered": covered}


def passes(p: dict, base: dict) -> bool:
    return (p["duplicates_per_covered"] < base["duplicates_per_covered"]
            and p["wrong_merge_rate"] <= base["wrong_merge_rate"] + WRONG_MERGE_MAX_UP
            and p["campaign_merges"] == 0 and p["coverage"] >= base["coverage"] - COVERAGE_MAX_DOWN)


def choose(worlds: list[dict]) -> dict:
    P = {k: pooled(worlds, k) for k in VARIANTS}
    base = P[BASELINE]
    alone = {"A": key(True, "chains", False), "B-chains": key(False, "chains_plus", False),
             "B-pairs": key(False, "pairs", False), "C": key(False, "chains", True)}
    ok = {name: passes(P[k], base) for name, k in alone.items()}
    gain = {name: base["duplicates_per_covered"] - P[k]["duplicates_per_covered"] for name, k in alone.items()}
    b = None
    if ok["B-chains"] and ok["B-pairs"]:
        b = "B-pairs" if gain["B-pairs"] - gain["B-chains"] > B_TIE else "B-chains"
    elif ok["B-chains"] or ok["B-pairs"]:
        b = "B-chains" if ok["B-chains"] else "B-pairs"
    chosen = [n for n in ("A", "C") if ok[n]] + ([b] if b else [])
    steps = []
    while chosen:
        k = key("A" in chosen, {"B-chains": "chains_plus", "B-pairs": "pairs"}.get(b if b in chosen else "", "chains"),
                "C" in chosen)
        steps.append({"options": list(chosen), "variant": k, "passes": passes(P[k], base),
                      **{m: round(P[k][m], 4) for m in ("duplicates_per_covered", "wrong_merge_rate", "coverage")},
                      "campaign_merges": P[k]["campaign_merges"]})
        if passes(P[k], base):
            break
        chosen.remove(min(chosen, key=lambda n: gain[n]))
    final = steps[-1]["variant"] if steps and steps[-1]["passes"] else BASELINE
    return {"pooled": P, "alone": {n: {"variant": k, "passes": ok[n], "gain": gain[n]} for n, k in alone.items()},
            "b_variant": b, "steps": steps, "chosen": final}


def choose_ingestion(worlds: list[dict]) -> dict:
    """ADR-053 D-2: candidates against the incidents_v4 default; fewer duplicates wins, chains_plus within 0.02."""
    P = {k: pooled(worlds, k) for k in [ING_BASELINE, ING_REFERENCE] + list(ING_CANDIDATES.values())}
    base = P[ING_BASELINE]
    ok = {n: passes(P[k], base) for n, k in ING_CANDIDATES.items()}
    dup = {n: P[k]["duplicates_per_covered"] for n, k in ING_CANDIDATES.items()}
    passing = [n for n in ING_CANDIDATES if ok[n]]
    pick = None
    if passing:
        pick = min(passing, key=lambda n: dup[n])
        if pick == "pairs+rule" and ok["chains_plus+rule"] and dup["chains_plus+rule"] - dup["pairs+rule"] <= B_TIE:
            pick = "chains_plus+rule"
    return {"pooled": P, "alone": {n: {"variant": k, "passes": ok[n], "gain": base["duplicates_per_covered"] - dup[n]}
                                   for n, k in ING_CANDIDATES.items()},
            "b_variant": None, "steps": [], "chosen": ING_CANDIDATES[pick] if pick else ING_BASELINE}


def _f(x, pct=False):
    return f"{100 * x:.1f} %" if pct else f"{x:.3f}"


def report(worlds: list[dict], title: str, sel: dict | None, compare: list[str]) -> str:
    lines = [f"# Duplicate incidents — {title}", "", f"Seeds {sorted({w['seed'] for w in worlds})}; realism "
             f"{sorted({w['realism'] for w in worlds})}; route-agnostic; `G` = 6 h, `H` = 0.", ""]
    lines += ["| variant | duplicates / covered | wrong merges | campaign + outage | coverage | purity | incidents |",
              "|---|---|---|---|---|---|---|"]
    for k in compare:
        for realism in sorted({w["realism"] for w in worlds}) + ["both"]:
            ws = worlds if realism == "both" else [w for w in worlds if w["realism"] == realism]
            p = pooled(ws, k)
            lines.append(f"| {k} · {realism} | {_f(p['duplicates_per_covered'])} | {_f(p['wrong_merge_rate'], True)} | "
                         f"{p['campaign_merges']} | {_f(p['coverage'], True)} | {_f(p['purity'], True)} | {p['incidents']} |")
    if sel:
        lines += ["", "Options alone (pooled, both realisms): " + "; ".join(
            f"{n}: {'passes' if v['passes'] else 'fails'}, gain {v['gain']:.3f}" for n, v in sel["alone"].items()),
                  f"B variant: {sel['b_variant']}; combination steps: {sel['steps']}; **chosen: {sel['chosen']}**"]
    for k in sorted({k for w in worlds for k in w["classified"]}):
        c = Counter(d["reason"] for w in worlds for d in w["classified"].get(k, []))
        lines += ["", f"Duplicates by reason, {k}: " + ", ".join(f"{r} {c.get(r, 0)}" for r in dup.REASONS)]
    for k in compare:
        hub, kinds = Counter(), Counter()
        for w in worlds:
            hub.update(w["variants"][k].get("ingestion_anchored", {}))
            kinds.update(w["variants"][k].get("dup_by_kind", {}))
        lines += ["", f"{k}: joins anchored by an ingestion member {dict(hub)}; duplicates of data_pipeline_issue "
                  f"{kinds.get('data_pipeline_issue', 0)}"]
    par = Counter()
    for w in worlds:
        par.update(w["parents"])
    secs = sum(w["seconds"] for w in worlds)
    lines += ["", f"Parent loci (first look): {dict(par)}",
              f"Pipeline seconds per world: median {sorted(w['seconds'] for w in worlds)[len(worlds) // 2]:.0f}; "
              f"parent-locus share of pipeline time {sum(w['parent_seconds'] for w in worlds) / secs:.0%} "
              "(0 when the protocol does not compute parent loci)", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true", help="seeds 11-20 with the chosen combination")
    ap.add_argument("--realism", nargs="*", default=["v1", "v2"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--seeds", type=int, nargs="*", default=None)
    ap.add_argument("--protocol", choices=("adr052", "ingestion"), default="adr052")
    a = ap.parse_args(argv)
    seeds = a.seeds or (VALIDATION if a.validate else TUNING)
    if set(seeds) & set(HELDOUT_SEEDS):
        print("refusing to evaluate on HELDOUT_SEEDS", file=sys.stderr)
        return 2
    prefix = "dup" if a.protocol == "adr052" else "ingestion"
    base_key = BASELINE if a.protocol == "adr052" else ING_BASELINE
    tuning_file = ROOT / "reports" / f"{prefix}_tuning.json"
    chosen = json.loads(tuning_file.read_text())["selection"]["chosen"] if a.validate else None
    classify_keys = (base_key, chosen) if a.validate else (base_key,)
    jobs = [(s, r, a.scale, classify_keys, a.protocol == "adr052") for r in a.realism for s in seeds]
    worlds = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for w in ex.map(one_world, jobs):
            worlds.append(w)
            print(f"seed {w['seed']:4} {w['realism']}: {w['seconds']} s (parent {w['parent_seconds']} s)", flush=True)
    out = ROOT / "reports" / (f"{prefix}_validation" if a.validate else f"{prefix}_tuning")
    sel = None if a.validate else (choose(worlds) if a.protocol == "adr052" else choose_ingestion(worlds))
    if a.validate:
        compare = [base_key, chosen] + ([ING_REFERENCE] + list(ING_CANDIDATES.values()) if a.protocol == "ingestion" else [])
    elif a.protocol == "ingestion":
        compare = [ING_BASELINE, ING_REFERENCE] + list(ING_CANDIDATES.values())
    else:
        compare = [BASELINE] + [v["variant"] for v in sel["alone"].values()] + (
            [sel["chosen"]] if sel["chosen"] not in (BASELINE,) else [])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({"worlds": worlds, "selection": sel, "chosen": chosen or sel["chosen"]},
                                                   indent=1, sort_keys=True, default=list))
    out.with_suffix(".md").write_text(report(worlds, "validation" if a.validate else "tuning", sel, list(dict.fromkeys(compare))))
    print(f"written {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
