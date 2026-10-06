#!/usr/bin/env python3
"""Stage 7 Phase 2 (ADR-049 D-9): investigations on DEV seeds. Writes reports/stage7_dev.{json,md}.

Per world (sim-1.3.0, randomized calendar): Stage 6 incidents route-agnostic (`all`) and with `baseline_v2`; each
incident investigated at creation by (0) prior only, (1) the deterministic control, (1b) the control without side-file
tools, (2) the fake pipeline — at every tool-call budget of the grid; plus a diagnostic control pass at +6 h.
The budget is chosen on seeds 1-10 (control, route-agnostic): the smallest budget whose pooled top-3 is within 1 point
of the uncapped run; everything is reported at that budget, tuning and validation seeds separately. Jev itself is not
evaluated (OQ-1). Refuses HELDOUT seeds.
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
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_detection_dev import _Chdb  # noqa: E402
from pulseos.detection import detect  # noqa: E402
from pulseos.evaluation import investigation as ev  # noqa: E402
from pulseos.evaluation.incidents import score_incidents  # noqa: E402
from pulseos.events import store  # noqa: E402
from pulseos.incidents import pipeline as inc_pipeline  # noqa: E402
from pulseos.investigation import pipeline as inv_pipeline  # noqa: E402
from pulseos.investigation.choosers import ControlChooser, JevChooser  # noqa: E402
from pulseos.investigation.config import InvestigationConfig  # noqa: E402
from pulseos.jev.client import BudgetedClient, JevBudget  # noqa: E402
from pulseos.jev.fake import FakeJevClient  # noqa: E402
from pulseos.simulation import clickhouse_load as chl  # noqa: E402
from pulseos.simulation.runner import generate  # noqa: E402
from pulseos.simulation.seeds import DEV_SEEDS, HELDOUT_SEEDS  # noqa: E402
from pulseos.simulation.world import WorldConfig  # noqa: E402

TUNING, VALIDATION = set(range(1, 11)), set(range(11, 21))
BUDGETS = (6, 9, 12, 50)  # 50 = uncapped in practice
SYSTEMS = ("control", "control_no_side", "fake")


def _investigate(c, run_id, s6, cands, w, system, budget, offset=0):
    cfg = InvestigationConfig(max_tool_calls=budget, use_side_files=(system != "control_no_side"))
    if system == "fake":
        factory = lambda v: JevChooser(FakeJevClient(), "fake-jev-0", v.as_of, tuple(v.anchor.evidence_ids))  # noqa: E731
    else:
        factory = lambda v: ControlChooser()  # noqa: E731
    t0 = time.perf_counter()
    r = inv_pipeline.run(c, "anomalyos", run_id, s6, cands, w.start, factory, cfg, offset_s=offset)
    return r, cfg, (time.perf_counter() - t0) / max(1, len(r.investigations))


def one_world(args) -> dict:
    seed, realism, scale = args
    work = Path(tempfile.mkdtemp(prefix=f"s7dev_{seed}_{realism}_"))
    try:
        w = WorldConfig(seed=seed, scale=scale, schedule="randomized", realism=realism)
        res = generate(w, "full", work / "run")
        c = _Chdb(str(work / "ch"))
        out = {"seed": seed, "realism": realism, "scale": scale}
        try:
            chl.load_run(c, work / "run", "anomalyos")
            store.load_norm(c, work / "run", "anomalyos")
            S, E = datetime.fromtimestamp(w.start, timezone.utc), datetime.fromtimestamp(w.end, timezone.utc)
            records = [g.to_dict() for g in res.ground_truth]
            by_key = {r["record_key"]: r for r in records}
            cands = detect(c, "anomalyos", res.run_id, S, E)
            full = [dataclasses.asdict(x) for x in cands]
            for source in ("all", "baseline"):
                s6 = inc_pipeline.run(c, "anomalyos", res.run_id, cands, w.start, w.end,
                                      BudgetedClient(FakeJevClient(), JevBudget(10**6)), "fake-jev-0", source,
                                      estimate_impact=False)
                e = s6.engine
                inc = score_incidents(e.incidents, e.incident_events, e.digest_groups, full, records, w.start, w.end)
                main = {x["incident_id"]: by_key.get(x["main"]) for x in inc["incidents"]}
                block = {"incidents": len(e.incidents)}
                views = inv_pipeline.views_at_creation(s6, cands)
                block["prior"] = [ev.score_prior(v, main.get(v.incident_id)) for v in views]
                systems = SYSTEMS if source == "all" else ("control",)
                for system in systems:
                    for budget in BUDGETS:
                        r, cfg, sec = _investigate(c, res.run_id, s6, cands, w, system, budget)
                        block[f"{system}|{budget}"] = [
                            ev.score(i, r.registries[i.investigation_id], r.reports[i.investigation_id],
                                     r.views[i.investigation_id], main.get(i.incident_id),
                                     s6.bundles.get(r.views[i.investigation_id].anchor.candidate_id), cfg, sec)
                            for i in r.investigations]
                if source == "all":
                    r, cfg, sec = _investigate(c, res.run_id, s6, cands, w, "control", 12, offset=6 * 3600)
                    block["control_6h|12"] = [
                        ev.score(i, r.registries[i.investigation_id], r.reports[i.investigation_id],
                                 r.views[i.investigation_id], main.get(i.incident_id),
                                 s6.bundles.get(r.views[i.investigation_id].anchor.candidate_id), cfg, sec)
                        for i in r.investigations]
                out[source] = block
        finally:
            c.close()
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _share(rows, key, where=lambda r: True):
    xs = [r[key] for r in rows if where(r)]
    return sum(bool(x) for x in xs) / len(xs) if xs else None


def choose_budget(results) -> dict:
    tun = [r for r in results if r["seed"] in TUNING]
    top3 = {}
    for b in BUDGETS:
        rows = [x for r in tun for x in r["all"][f"control|{b}"]]
        top3[b] = _share(rows, "top3", lambda x: not x["false_positive_incident"]) or 0.0
    uncapped = top3[max(BUDGETS)]
    chosen = min(b for b in BUDGETS if top3[b] >= uncapped - 0.01)
    return {"top3_by_budget": top3, "chosen": chosen}


def _f(x, pct=False):
    return "—" if x is None else (f"{100 * x:.0f} %" if pct else f"{x:.2f}")


def report(results, tuning) -> str:
    b = tuning["chosen"]
    inc = lambda x: not x["false_positive_incident"]  # noqa: E731
    lines = ["# Stage 7 — investigations on DEV seeds", "",
             "**Jev evaluation: blocked — no live access** (OQ-1). `fake` is a pipeline check, not a model evaluation.",
             f"Seeds {sorted({r['seed'] for r in results})}; sim-1.3.0; randomized calendar; tool-call budget **{b}** "
             f"(chosen on seeds 1–10: top-3 by budget {', '.join(f'{k}: {100 * v:.0f} %' for k, v in tuning['top3_by_budget'].items())}).", "",
             "## Systems (route-agnostic incidents)", "",
             "| system | seeds | realism | investigations | top-1 | top-3 | contradiction kept | FP incidents → not a cause | narrowed locus hit | Stage 4 top-5 hit | cites honest side signal | decoy supports leader | valid reports | disconfirming ok | repeats | over budget |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for system in ("prior", "control", "control_no_side", "fake", "control_6h"):
        for name, seeds in (("tuning 1–10", TUNING), ("**validation 11–20**", VALIDATION)):
            for realism in sorted({r["realism"] for r in results}):
                key = "prior" if system == "prior" else (f"{system}|{b}" if system != "control_6h" else "control_6h|12")
                rows = [x for r in results if r["seed"] in seeds and r["realism"] == realism for x in r["all"][key]]
                full = system != "prior"
                lines.append(
                    f"| {system} | {name} | realism_{realism} | {len(rows)} | {_f(_share(rows, 'top1', inc), 1)} | "
                    f"{_f(_share(rows, 'top3', inc), 1)} | "
                    f"{_f(_share(rows, 'contradiction_kept', lambda x: x['truth_not_leader']), 1) if full else '—'} | "
                    f"{_f(_share(rows, 'fp_concluded_not_a_cause', lambda x: x['false_positive_incident']), 1)} | "
                    + (f"{_f(_share(rows, 'narrowed_hit', lambda x: x['has_main']), 1)} | "
                       f"{_f(_share(rows, 'stage4_top5_hit', lambda x: x['has_main']), 1)} | "
                       f"{_f(_share(rows, 'cited_honest', lambda x: x['has_side_signals']), 1)} | "
                       f"{_f(_share(rows, 'decoy_supports_leader'), 1)} | {_f(_share(rows, 'report_valid'), 1)} | "
                       f"{_f(_share(rows, 'disconfirming_ok'), 1)} | {sum(x['repeats'] for x in rows)} | "
                       f"{sum(x['over_budget'] for x in rows)} |" if full else "— | — | — | — | — | — | — | — |"))
    lines += ["", "## Control with `baseline_v2` incidents (validation 11–20)", "",
              "| realism | investigations | top-1 | top-3 | FP → not a cause |", "|---|---|---|---|---|"]
    for realism in sorted({r["realism"] for r in results}):
        rows = [x for r in results if r["seed"] in VALIDATION and r["realism"] == realism for x in r["baseline"][f"control|{b}"]]
        lines.append(f"| realism_{realism} | {len(rows)} | {_f(_share(rows, 'top1', inc), 1)} | {_f(_share(rows, 'top3', inc), 1)} | "
                     f"{_f(_share(rows, 'fp_concluded_not_a_cause', lambda x: x['false_positive_incident']), 1)} |")
    lines += ["", "## Control by scenario kind (validation, realism_v1)", "",
              "| kind | n | top-1 | top-3 | leader most often | stop reasons |", "|---|---|---|---|---|---|"]
    rows = [x for r in results if r["seed"] in VALIDATION and r["realism"] == "v1" for x in r["all"][f"control|{b}"]]
    for kind in sorted({x["scenario_kind"] or "(no record)" for x in rows}):
        xs = [x for x in rows if (x["scenario_kind"] or "(no record)") == kind]
        lead = Counter(x["leader"] for x in xs).most_common(1)[0][0]
        stops = dict(Counter(x["stop_reason"] for x in xs).most_common(3))
        lines.append(f"| {kind} | {len(xs)} | {_f(_share(xs, 'top1', inc), 1)} | {_f(_share(xs, 'top3', inc), 1)} | {lead} | {stops} |")
    cost = [x for r in results for x in r["all"][f"control|{b}"]]
    fake = [x for r in results for x in r["all"][f"fake|{b}"]]
    secs = sorted(x["seconds"] for x in cost if x["seconds"] is not None)
    lines += ["", "## Cost (control, all seeds)", "",
              f"- steps median {median(x['steps'] for x in cost)}, tool calls median {median(x['tool_calls'] for x in cost)}, "
              f"evidence median {median(x['evidence'] for x in cost)}; fake Jev calls median {median(x['jev_calls'] for x in fake)}",
              f"- measured seconds per investigation: median {median(secs):.2f}, p95 {secs[int(0.95 * (len(secs) - 1))]:.2f}",
              f"- foreign tool calls (outside the registry): {sum(x['foreign_tools'] for x in cost + fake)}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=list(DEV_SEEDS))
    ap.add_argument("--realism", nargs="*", default=["v1", "v2"])
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "stage7_dev")
    a = ap.parse_args(argv)
    if set(a.seeds) & set(HELDOUT_SEEDS):
        print("refusing to evaluate on HELDOUT_SEEDS", file=sys.stderr)
        return 2
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(one_world, [(s, r, a.scale) for r in a.realism for s in a.seeds]):
            results.append(res)
            print(f"seed {res['seed']:4} {res['realism']}: {res['all']['incidents']} incidents", flush=True)
    tuning = choose_budget(results) if any(r["seed"] in TUNING for r in results) else {"top3_by_budget": {}, "chosen": 12}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(json.dumps({"tuning": tuning, "results": results}, indent=1, default=str))
    a.out.with_suffix(".md").write_text(report(results, tuning))
    print(f"written {a.out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
