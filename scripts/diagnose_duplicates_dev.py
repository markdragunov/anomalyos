"""Scope-dimension task, Phase 0: why duplicate incidents exist (evaluation only, no product change).

For each DEV world: Stage 3 → Stage 4/5 → Stage 6 event stream (route-agnostic, as the Stage 6 headline), refolded by
``evaluation.duplicates.RecordingEngine`` under two configurations — the Stage 6 choice (``G`` = 6 h, ``H`` = 0,
``reports/stage6_tuning.json``) and the ``IncidentConfig`` default (1 h / 1 h) that the product pipeline and the
Stage 7 evaluation used. Every duplicate gets the first correlation rule that kept it apart; first-look Stage 4 loci
are compared with the root-cause locus. Writes ``reports/scope_phase0.{json,md}``. Tuning seeds 1-10 only by default;
refuses HELDOUT.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_detection_dev import _Chdb  # noqa: E402
from eval_incidents_dev import with_hysteresis  # noqa: E402
from pulseos.detection import detect  # noqa: E402
from pulseos.evaluation import duplicates as dup  # noqa: E402
from pulseos.evaluation.incidents import score_incidents  # noqa: E402
from pulseos.events import store  # noqa: E402
from pulseos.incidents import pipeline  # noqa: E402
from pulseos.incidents.config import HOUR, IncidentConfig  # noqa: E402
from pulseos.jev.client import BudgetedClient, JevBudget  # noqa: E402
from pulseos.jev.fake import FakeJevClient  # noqa: E402
from pulseos.simulation import clickhouse_load as chl  # noqa: E402
from pulseos.simulation.runner import generate  # noqa: E402
from pulseos.simulation.seeds import HELDOUT_SEEDS  # noqa: E402
from pulseos.simulation.world import WorldConfig  # noqa: E402

CONFIGS = {"G6h_H0": (6 * HOUR, 0), "default_G1h_H1h": (HOUR, HOUR)}


def one_world(args) -> dict:
    seed, realism, scale = args
    work = Path(tempfile.mkdtemp(prefix=f"scope0_{seed}_{realism}_"))
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
            jev = BudgetedClient(FakeJevClient(), JevBudget(max_calls=10**6))
            pr = pipeline.run(client, "anomalyos", res.run_id, cands, w.start, w.end, jev, "fake-jev-0", "all",
                              IncidentConfig(gap_s=6 * HOUR, hysteresis_s=0), estimate_impact=False)
            out = {"seed": seed, "realism": realism,
                   "loci": dup.first_look_loci(pr.events, full, records, w.end)}
            for name, (gap, hyst) in CONFIGS.items():
                cfg = IncidentConfig(gap_s=gap, hysteresis_s=hyst, nesting="chains", parent_locus=False,
                                     renewal_with_approval=False, ingestion_anchors_others=True)  # Gate 0 rules
                eng = dup.RecordingEngine(cfg)
                r = eng.run(with_hysteresis(pr.events, hyst))
                sc = score_incidents(r.incidents, r.incident_events, r.digest_groups, full, records, w.start, w.end)
                rows = dup.classify(eng, sc["incidents"], records, cfg)
                out[name] = {"duplicates": rows, "scored_duplicates": sc["counts"]["duplicates"],
                             "covered": sc["counts"]["covered"], "incidents": sc["summary"]["incidents"],
                             "wrong_merges": sc["counts"]["wrong_merges"],
                             "campaign_merges": sc["summary"]["campaign_merges"]}
        finally:
            client.close()
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _pct(n, d):
    return f"{100 * n / d:.0f} %" if d else "—"


def report(results: list[dict]) -> str:
    lines = ["# Scope-dimension task — Phase 0 diagnosis (tuning seeds)", "",
             f"Seeds {sorted({r['seed'] for r in results})}; realism {sorted({r['realism'] for r in results})}; "
             "route-agnostic Stage 6 events (every decided candidate treated as `INCIDENT`).", ""]
    for name in CONFIGS:
        for realism in sorted({r["realism"] for r in results}):
            rs = [r for r in results if r["realism"] == realism]
            dups = [d for r in rs for d in r[name]["duplicates"] if d["counted"]]
            scored = sum(r[name]["scored_duplicates"] for r in rs)
            covered = sum(r[name]["covered"] for r in rs)
            reasons = Counter(d["reason"] for d in dups)
            lines += [f"## {name} · realism_{realism}", "",
                      f"Incidents {sum(r[name]['incidents'] for r in rs)}, covered records {covered}, duplicates "
                      f"{len(dups)} (Stage 6 metric: {scored}; per covered record {scored / covered:.2f}), wrong merges "
                      f"{sum(r[name]['wrong_merges'] for r in rs)}, campaign + outage merges "
                      f"{sum(r[name]['campaign_merges'] for r in rs)}.", "",
                      "| reason | duplicates | share |", "|---|---|---|"]
            lines += [f"| {k} | {reasons.get(k, 0)} | {_pct(reasons.get(k, 0), len(dups))} |" for k in dup.REASONS]
            kinds = sorted({d["scenario_kind"] for d in dups})
            lines += ["", "| scenario kind | duplicates | " + " | ".join(dup.REASONS) + " |",
                      "|---|---|" + "---|" * len(dup.REASONS)]
            for k in kinds:
                c = Counter(d["reason"] for d in dups if d["scenario_kind"] == k)
                lines.append(f"| {k} | {sum(c.values())} | " + " | ".join(str(c.get(x, 0) or "") for x in dup.REASONS) + " |")
            pairs = Counter((d["reason"], tuple(sorted(x[0] for x in d["seed_scope"])),
                             tuple(sorted(x[0] for x in d["seed_locus"])), d["seed_metric"])
                            for d in dups if d["reason"] in ("scope", "chain", "disjoint"))
            dj = Counter(d["disjoint_kind"] for d in dups if d["reason"] == "disjoint")
            lines += ["", f"Disjoint: {dict(dj)} (conflict = a shared dimension with different values).", ""]
            mp = Counter((d["reason"], d["seed_metric"], tuple(x[0] for x in d["seed_locus"]), d["member_metric"],
                          tuple(x[0] for x in d["member_locus"]))
                         for d in dups if d["reason"] in ("chain", "scope", "disjoint"))
            lines += ["Most frequent pairs (reason · seed metric, locus dims → closest member metric, locus dims):", ""]
            lines += [f"- {n} × {r} · {sm} {list(sl) or 'global'} → {mm} {list(ml) or 'global'}"
                      for (r, sm, sl, mm, ml), n in mp.most_common(15)]
            lines += ["", "Most frequent seed shapes (reason · scope dims · locus dims · metric):", ""]
            lines += [f"- {n} × {r} · {list(s) or 'global'} · {list(l) or 'global'} · {m}"
                      for (r, s, l, m), n in pairs.most_common(12)]
            lines.append("")
    lines += ["## First-look Stage 4 locus vs root cause (first matched candidate per incident record)", ""]
    for realism in sorted({r["realism"] for r in results}):
        loci = [x for r in results if r["realism"] == realism for x in r["loci"]]
        res = Counter(x["result"] for x in loci)
        over = [x for x in loci if x["result"] == "over_specific"]
        lines += [f"realism_{realism}: {len(loci)} records — " + ", ".join(f"{k} {_pct(v, len(loci))}" for k, v in sorted(res.items()))
                  + f"; over-specific with only scope-inherited extra dimensions: {sum(x['extra_from_scope'] for x in over)} / {len(over)}", "",
                  "| scenario kind | n | exact | over-specific (from scope) | coarse | wrong |", "|---|---|---|---|---|---|"]
        for k in sorted({x["scenario_kind"] for x in loci}):
            xs = [x for x in loci if x["scenario_kind"] == k]
            c = Counter(x["result"] for x in xs)
            fs = sum(x["extra_from_scope"] for x in xs)
            lines.append(f"| {k} | {len(xs)} | {c['exact']} | {c['over_specific']} ({fs}) | {c['coarse']} | {c['wrong']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=list(range(1, 11)))
    ap.add_argument("--realism", nargs="*", default=["v1", "v2"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "scope_phase0")
    a = ap.parse_args(argv)
    if set(a.seeds) & set(HELDOUT_SEEDS):
        print("refusing to evaluate on HELDOUT_SEEDS", file=sys.stderr)
        return 2
    jobs = [(s, r, a.scale) for r in a.realism for s in a.seeds]
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(one_world, jobs):
            results.append(res)
            print(f"seed {res['seed']:4} {res['realism']}: {len(res['G6h_H0']['duplicates'])} duplicates", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(json.dumps(results, indent=1, sort_keys=True, default=list))
    a.out.with_suffix(".md").write_text(report(results))
    print(f"written {a.out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
