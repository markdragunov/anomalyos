#!/usr/bin/env python3
"""Stage 3 Phase 2: run detection on DEV seeds and score it (ADR-034 D-7). Writes reports/stage3_dev.{json,md}.

Uses embedded ClickHouse (chdb, optional, not a project dependency) when installed, else the server from
CLICKHOUSE_* settings. HELDOUT seeds are refused on purpose: they belong to Stage 9.
Usage: python scripts/eval_detection_dev.py [--seeds 1 2 3] [--realism v1 v2] [--scale 1.0] [--workers 4]
"""

from __future__ import annotations

import argparse
import json
import os
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

from anomalyos.detection import DetectorConfig, detect  # noqa: E402
from anomalyos.evaluation.detection import score  # noqa: E402
from anomalyos.events import normalize as nz  # noqa: E402
from anomalyos.events import store  # noqa: E402
from anomalyos.simulation import clickhouse_load as chl  # noqa: E402
from anomalyos.simulation.runner import generate  # noqa: E402
from anomalyos.simulation.seeds import DEV_SEEDS, HELDOUT_SEEDS  # noqa: E402
from anomalyos.simulation.world import WorldConfig  # noqa: E402


class _Chdb:
    """Minimal clickhouse_connect-like adapter over chdb (command, raw_insert) plus a QueryRunner."""

    def __init__(self, path: str):
        from chdb import session

        self.s = session.Session(path)
        self.tmp = tempfile.mkdtemp()
        self.types = {"events": chl.EVENT_COLUMNS, "ground_truth": chl.TRUTH_COLUMNS, "runs": chl.RUN_COLUMNS,
                      "events_norm": nz.NORM_COLUMNS}

    def command(self, sql: str):
        text = self.s.query(sql, "TabSeparated").bytes().decode().strip()
        return int(text) if text.isdigit() else text

    def raw_insert(self, table, column_names, insert_block: bytes, fmt: str = "JSONEachRow"):
        path = os.path.join(self.tmp, "block.jsonl")
        with open(path, "wb") as f:
            f.write(insert_block)
        tmap = {n: t.split(" CODEC")[0] for n, t in self.types[table.split(".")[1]]}
        cols = list(column_names)
        structure = ", ".join(f"`{c}` {tmap[c]}" for c in cols).replace("'", "\\'")
        col_list = ", ".join(f"`{c}`" for c in cols)
        self.s.query(f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM file('{path}', '{fmt}', '{structure}')")

    def rows(self, sql, params):
        def lit(v):
            if isinstance(v, (list, tuple)):
                return "[" + ",".join("'" + str(x).replace("\\", "\\\\").replace("'", "\\'") + "'" if isinstance(x, str) else str(x) for x in v) + "]"
            return str(v)
        out = self.s.query(sql, "JSONCompact", params={k: lit(v) for k, v in params.items()})
        return [tuple(r) for r in json.loads(out.bytes().decode())["data"]]

    def close(self):
        self.s.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def one_world(args: tuple[int, str, float]) -> dict:
    seed, realism, scale = args
    t0 = time.time()
    work = Path(tempfile.mkdtemp(prefix=f"s3dev_{seed}_{realism}_"))
    try:
        w = WorldConfig(seed=seed, scale=scale, schedule="randomized", realism=realism)
        res = generate(w, "full", work / "run")
        db = "anomalyos"
        client = _Chdb(str(work / "ch"))
        try:
            chl.load_run(client, work / "run", db)
            store.load_norm(client, work / "run", db)
            start, end = datetime.fromtimestamp(w.start, timezone.utc), datetime.fromtimestamp(w.end, timezone.utc)
            records = [g.to_dict() for g in res.ground_truth]
            out = {"seed": seed, "realism": realism, "scale": scale, "run_id": res.run_id, "systems": {}}
            for system in ("main", "static"):
                cands = detect(client, db, res.run_id, start, end, system=system)
                out["systems"][system] = score(cands, records, w.start, w.end)
        finally:
            client.close()
        out["seconds"] = round(time.time() - t0, 1)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _fmt_h(s):
    return "—" if s is None else f"{s / 3600:.1f} h"


def report(results: list[dict], cfg: DetectorConfig) -> str:
    lines = ["# Stage 3 — detection on DEV seeds", "",
             f"Seeds: {sorted({r['seed'] for r in results})}; scale {results[0]['scale']}; randomized calendar; generated "
             f"{datetime.now(timezone.utc).date()} (report only, not committed).", "",
             "## Overall", "", "| realism | system | recall | detected / scored | oracle-null | warm-up misses | other misses | "
             "latency median | latency p90 | FP / day | FP unmatched / suppress / unchanged | watch hits |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for realism in sorted({r["realism"] for r in results}):
        for system in ("main", "static"):
            rs = [r["systems"][system] for r in results if r["realism"] == realism]
            sc = sum(x["summary"]["incidents_scored"] for x in rs); dt = sum(x["summary"]["detected"] for x in rs)
            lat = sorted(rr["latency_s"] for x in rs for rr in x["records"] if rr["outcome"] == "detected")
            fp = sum(x["summary"]["false_positives"] for x in rs)
            days = sum(x["summary"]["false_positives"] / x["summary"]["false_positives_per_day"] if x["summary"]["false_positives"] else 25 for x in rs)
            by = {k: sum(x["summary"]["fp_by_class"][k] for x in rs) for k in ("fp_unmatched", "fp_suppress", "fp_unchanged_metric")}
            lines.append(f"| {realism} | {system} | {dt / sc:.2f} | {dt} / {sc} | {sum(x['summary']['incidents_oracle_null'] for x in rs)} | "
                         f"{sum(x['summary']['missed_warm_up'] for x in rs)} | {sum(x['summary']['missed'] for x in rs)} | "
                         f"{_fmt_h(median(lat) if lat else None)} | {_fmt_h(lat[int(0.9 * (len(lat) - 1))] if lat else None)} | "
                         f"{fp / days:.2f} | {by['fp_unmatched']} / {by['fp_suppress']} / {by['fp_unchanged_metric']} | "
                         f"{sum(x['summary']['watch_hits'] for x in rs)} |")
    lines += ["", "## By scenario kind (main system)", "", "| realism | kind | detected / scored | warm-up | missed | latency median |", "|---|---|---|---|---|---|"]
    for realism in sorted({r["realism"] for r in results}):
        kinds: dict[str, list] = {}
        for r in results:
            if r["realism"] != realism:
                continue
            for rr in r["systems"]["main"]["records"]:
                kinds.setdefault(rr["scenario_kind"], []).append(rr)
        for kind, rows in sorted(kinds.items()):
            sc = [x for x in rows if x["outcome"] != "oracle_null"]
            det = [x for x in sc if x["outcome"] == "detected"]
            lat = sorted(x["latency_s"] for x in det)
            lines.append(f"| {realism} | {kind} | {len(det)} / {len(sc)} | {sum(x['outcome'] == 'warm_up' for x in sc)} | "
                         f"{sum(x['outcome'] == 'missed' for x in sc)} | {_fmt_h(median(lat) if lat else None)} |")
    lines += ["", "## False positives by suppress kind (main system)", ""]
    sup: dict[tuple, int] = {}
    for r in results:
        for c in r["systems"]["main"]["candidates"]:
            if c["class"] == "fp_suppress":
                for k in c["detail"]:
                    sup[(r["realism"], k)] = sup.get((r["realism"], k), 0) + 1
    lines += [f"- {realism} / {k}: {n}" for (realism, k), n in sorted(sup.items())] or ["- none"]
    lines += ["", "## Parameters", "", "```", repr(cfg), "```"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=list(DEV_SEEDS))
    ap.add_argument("--realism", nargs="*", default=["v1", "v2"])
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "stage3_dev")
    a = ap.parse_args(argv)
    if set(a.seeds) & set(HELDOUT_SEEDS):
        print("refusing to evaluate on HELDOUT_SEEDS in Stage 3", file=sys.stderr)
        return 2
    jobs = [(s, r, a.scale) for r in a.realism for s in a.seeds]
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(one_world, jobs):
            results.append(res)
            s = res["systems"]["main"]["summary"]
            print(f"seed {res['seed']:4} {res['realism']}: recall {s['recall']:.2f} fp/day {s['false_positives_per_day']:.2f} "
                  f"({res['seconds']} s)", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(json.dumps(results, indent=1, sort_keys=True))
    a.out.with_suffix(".md").write_text(report(results, DetectorConfig()))
    print(f"written {a.out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
