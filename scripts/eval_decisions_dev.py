#!/usr/bin/env python3
"""Stage 5 Phase 2: Mode A on DEV seeds (ADR-039 D-9). Writes reports/stage5_dev.{json,md}.

Per world: generate (randomized calendar, scale 1.0), load, normalize, Stage 3 detection, Mode A at first look
(Stage 4 on the as-of window, JevState, client, verifier, policy_v1) and the no-Jev baseline_v1 on the same states.
There is no Jev access (OQ-1): the client is the deterministic fake, so Jev itself is **not evaluated** — the report
shows the baseline (tuning seeds 1-10 and validation seeds 11-20 separately), Stage 4 quality at first look, and
technical pipeline figures. Refuses HELDOUT seeds.
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

from eval_cohorts_dev import _slim  # noqa: E402
from eval_detection_dev import _Chdb  # noqa: E402
from anomalyos.detection import detect  # noqa: E402
from anomalyos.evaluation.cohorts import score_cohorts  # noqa: E402
from anomalyos.evaluation.decisions import score_decisions  # noqa: E402
from anomalyos.events import store  # noqa: E402
from anomalyos.jev.client import BudgetedClient, JevBudget  # noqa: E402
from anomalyos.jev.fake import FakeJevClient  # noqa: E402
from anomalyos.jev.state import JevState  # noqa: E402
from anomalyos.policy import baseline, mode_a  # noqa: E402
from anomalyos.simulation import clickhouse_load as chl  # noqa: E402
from anomalyos.simulation.runner import generate  # noqa: E402
from anomalyos.simulation.seeds import DEV_SEEDS, HELDOUT_SEEDS  # noqa: E402
from anomalyos.simulation.world import WorldConfig  # noqa: E402

TUNING, VALIDATION = set(range(1, 11)), set(range(11, 21))
PINNED = "fake-jev-0"


def _state(record) -> JevState | None:
    if record.state_json == "{}":
        return None
    s = json.loads(record.state_json)
    return JevState(**{k: tuple(v) if isinstance(v, list) else v for k, v in s.items()})


def one_world(args) -> dict:
    seed, realism, scale = args
    work = Path(tempfile.mkdtemp(prefix=f"s5dev_{seed}_{realism}_"))
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
            jev = BudgetedClient(FakeJevClient(), JevBudget(max_calls=100_000))
            t0 = time.time()
            decisions = mode_a.run(client, "anomalyos", res.run_id, cands, w.start, jev, PINNED)
            seconds = time.time() - t0
        finally:
            client.close()
        full = {c.anomaly_id: dataclasses.asdict(c) for c in cands}
        rows = []
        for d in decisions:
            r, st = d.record, _state(d.record)
            rows.append({"candidate": full[r.candidate_id], "verified": r.verified, "reasons": r.verifier_reasons,
                         "answers": json.loads(r.answers_json),
                         "routes": {"policy_v1_fake": r.route,
                                    "baseline_v1": baseline.route(st)[0] if st is not None else "DIGEST"}})
        analyses = [_slim(d.analysis) for d in decisions if d.analysis is not None]
        asof = [dataclasses.asdict(d.candidate) for d in decisions]
        states = [json.loads(d.record.state_json) for d in decisions if d.record.state_json != "{}"]
        return {
            "seed": seed, "realism": realism, "scale": scale,
            "decisions": score_decisions(rows, records, w.start, w.end),
            "stage4_first_look": score_cohorts(analyses, asof, records, w.end)["summary"],
            "stage4_labels": dict(Counter(a["label"] for a in analyses)),
            "pipeline": {"decisions": len(decisions), "candidates": len(cands), "calls": jev.calls,
                         "seconds": round(seconds, 1), "audit_records": len({d.record.decision_id for d in decisions}),
                         "state_bytes": sorted(len(d.record.state_json.encode()) for d in decisions),
                         "not_provided": {k: sum(s[k] == "not_provided" for s in states)
                                          for k in ("change_type", "locus_share", "related_metrics", "impact")},
                         "rules": dict(Counter(d.record.rule for d in decisions))},
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _f(x, pct=False):
    return "—" if x is None else (f"{100 * x:.0f} %" if pct else f"{x:.2f}")


def _baseline_rows(results, seeds, realism):
    rs = [r for r in results if r["seed"] in seeds and r["realism"] == realism]
    rows = [x for r in rs for x in r["decisions"]["rows"]]
    days = sum(r["decisions"]["days"] for r in rs)
    def per_day(st, route):
        return sum(1 for x in rows if x["status"] == st and x["routes"]["baseline_v1"] == route) / days
    routed = sum(r["decisions"]["systems"]["baseline_v1"]["incidents_routed_incident"] for r in rs)
    scored = sum(r["decisions"]["incidents_scored"] for r in rs)
    acc_n = [x for x in rows if x["status"] != "unmatched"]
    expected = {"incident": "INCIDENT", "watch": "DIGEST", "suppress": "IGNORE"}
    return {
        "INCIDENT/day": sum(1 for x in rows if x["routes"]["baseline_v1"] == "INCIDENT") / days,
        "DIGEST/day": sum(1 for x in rows if x["routes"]["baseline_v1"] == "DIGEST") / days,
        "IGNORE/day": sum(1 for x in rows if x["routes"]["baseline_v1"] == "IGNORE") / days,
        "recall": routed / scored if scored else None,
        "accuracy": sum(x["routes"]["baseline_v1"] == expected[x["status"]] for x in acc_n) / len(acc_n) if acc_n else None,
        "unmatched→INCIDENT/day": per_day("unmatched", "INCIDENT"), "suppress→INCIDENT/day": per_day("suppress", "INCIDENT"),
        "watch→INCIDENT/day": per_day("watch", "INCIDENT"),
    }


def report(results: list[dict]) -> str:
    lines = ["# Stage 5 — Mode A on DEV seeds", "",
             "**Jev evaluation: blocked — no live access** (OQ-1). The client is the deterministic fake; its routes are",
             "pipeline diagnostics, not a model evaluation. Candidate-level routes, not pages (grouping is Stage 6).", "",
             f"Seeds {sorted({r['seed'] for r in results})}; scale {results[0]['scale']}; randomized calendar; decisions at first look (ADR-039 D-0).", "",
             "## No-Jev baseline (`baseline_v1`, not tuned)", "",
             "| seeds | realism | INCIDENT / day | DIGEST / day | IGNORE / day | incident recall at INCIDENT | route accuracy (matched) | unmatched → INCIDENT / day | suppress → INCIDENT / day | watch → INCIDENT / day |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, seeds in (("tuning 1–10", TUNING), ("**validation 11–20**", VALIDATION)):
        for realism in sorted({r["realism"] for r in results}):
            m = _baseline_rows(results, seeds, realism)
            lines.append(f"| {name} | realism_{realism} | {_f(m['INCIDENT/day'])} | {_f(m['DIGEST/day'])} | {_f(m['IGNORE/day'])} | "
                         f"{_f(m['recall'], 1)} | {_f(m['accuracy'], 1)} | {_f(m['unmatched→INCIDENT/day'])} | "
                         f"{_f(m['suppress→INCIDENT/day'])} | {_f(m['watch→INCIDENT/day'])} |")
    lines += ["", "### Baseline routes of matched incident candidates by scenario kind (validation, realism_v1)", "",
              "| kind | candidates | INCIDENT | DIGEST | IGNORE |", "|---|---|---|---|---|"]
    kinds: dict[str, Counter] = {}
    for r in results:
        if r["seed"] in VALIDATION and r["realism"] == "v1":
            for x in r["decisions"]["rows"]:
                if x["status"] == "incident":
                    kinds.setdefault(x["scenario_kind"], Counter())[x["routes"]["baseline_v1"]] += 1
    for k, c in sorted(kinds.items()):
        lines.append(f"| {k} | {sum(c.values())} | {c['INCIDENT']} | {c['DIGEST']} | {c['IGNORE']} |")
    lines += ["", "## Stage 4 at first look (the real input of Mode A)", "",
              "| realism | top-1 exact | equivalence-aware | label (incidents) | `insufficient_data` share |", "|---|---|---|---|---|"]
    for realism in sorted({r["realism"] for r in results}):
        rs = [r for r in results if r["realism"] == realism]
        def mean(key):
            xs = [r["stage4_first_look"][key] for r in rs if r["stage4_first_look"][key] is not None]
            return sum(xs) / len(xs) if xs else None
        labels = Counter()
        for r in rs:
            labels.update(r["stage4_labels"])
        lines.append(f"| realism_{realism} | {_f(mean('top1_exact'), 1)} | {_f(mean('top1_equivalent_exact'), 1)} | "
                     f"{_f(mean('label_accuracy_incidents'), 1)} | {_f(labels['insufficient_data'] / max(1, sum(labels.values())), 1)} |")
    p = [r["pipeline"] for r in results]
    sizes = sorted(s for x in p for s in x["state_bytes"])
    rules = Counter()
    for x in p:
        rules.update(x["rules"])
    reasons = Counter()
    for r in results:
        reasons.update(r["decisions"]["answers"]["failure_reasons"])
    n = sum(x["decisions"] for x in p)
    np = Counter()
    for x in p:
        np.update(x["not_provided"])
    lines += ["", "## Pipeline (fake client — technical figures only)", "",
              f"- decisions {n} (all decidable candidates), audit records {sum(x['audit_records'] for x in p)}, calls {sum(x['calls'] for x in p)}, live calls 0",
              f"- state bytes: min {sizes[0]}, median {median(sizes)}, max {max(sizes)} (budget 2,048)",
              f"- verified {_f(1 - sum(reasons.values()) / n, 1)}; unverified reasons: {dict(reasons.most_common())}",
              f"- `not_provided` share: " + ", ".join(f"{k} {_f(v / n, 1)}" for k, v in np.items()),
              f"- policy rules hit (fake answers): {dict(rules.most_common())}",
              f"- seconds per world for Mode A (Stage 4 + decisions): median {median(x['seconds'] for x in p)}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=list(DEV_SEEDS))
    ap.add_argument("--realism", nargs="*", default=["v1", "v2"])
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "stage5_dev")
    a = ap.parse_args(argv)
    if set(a.seeds) & set(HELDOUT_SEEDS):
        print("refusing to evaluate on HELDOUT_SEEDS", file=sys.stderr)
        return 2
    jobs = [(s, r, a.scale) for r in a.realism for s in a.seeds]
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(one_world, jobs):
            results.append(res)
            print(f"seed {res['seed']:4} {res['realism']}: {res['pipeline']['decisions']} decisions "
                  f"({res['pipeline']['seconds']} s)", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(json.dumps(results, indent=1, sort_keys=True, default=list))
    a.out.with_suffix(".md").write_text(report(results))
    print(f"written {a.out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
