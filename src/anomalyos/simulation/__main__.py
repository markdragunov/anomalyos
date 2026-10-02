"""CLI: ``python -m anomalyos.simulation {generate,validate,load}`` (also ``anomalyos-sim``).

Exit codes: 0 ok · 1 validation/check failures · 2 usage/config error · 3 ClickHouse error.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

from .runner import generate
from .scenarios import PRESETS
from .validate import EventValidator, validate_ground_truth
from .world import WorldConfig


def _validate_dir(d: Path) -> int:
    v = EventValidator()
    with gzip.open(d / "events.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            v.feed(json.loads(line), line)
    rep = v.finish()
    manifest = json.loads((d / "manifest.json").read_text())
    gt = json.loads((d / "ground_truth.json").read_text())["records"]
    grep = validate_ground_truth(gt, [s["scenario_id"] for s in manifest["scenarios"]])
    print(f"events: {rep.events:,} checked · objects {rep.objects} · errors {rep.error_count}")
    print(f"ground truth: {len(gt)} records · errors {grep.error_count}")
    for e in (rep.errors + grep.errors)[:50]:
        print("  ✗", e)
    if rep.events != manifest["events"]:
        print(f"  ✗ manifest says {manifest['events']} events")
        return 1
    return 0 if rep.ok and grep.ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="anomalyos-sim", description="Stage 1 synthetic billing world")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate a run directory")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--preset", choices=sorted(PRESETS), default="full")
    g.add_argument("--scale", type=float, default=1.0, help="1.0 ≈ 1.37M events over 28 days")
    g.add_argument("--days", type=int, default=28)
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--overwrite", action="store_true")
    g.add_argument("--validate", action="store_true", help="validate the written run afterwards")
    v = sub.add_parser("validate", help="validate a run directory")
    v.add_argument("--in", dest="inp", type=Path, required=True)
    ld = sub.add_parser("load", help="load a run directory into ClickHouse (uses load_settings)")
    ld.add_argument("--in", dest="inp", type=Path, required=True)
    ld.add_argument("--replace", action="store_true")
    a = p.parse_args(argv)

    try:
        if a.cmd == "generate":
            res = generate(WorldConfig(seed=a.seed, scale=a.scale, days=a.days), a.preset, a.out, overwrite=a.overwrite)
            print(res.summary())
            print(f"written to {a.out}/ (events.jsonl.gz, ground_truth.json, manifest.json)")
            return _validate_dir(a.out) if a.validate else 0
        if a.cmd == "validate":
            return _validate_dir(a.inp)
        if a.cmd == "load":
            from .clickhouse_load import connect, load_run, target_from_settings

            t = target_from_settings()
            try:
                out = load_run(connect(t), a.inp, t.database, t.truth_database, replace=a.replace)
            except Exception as exc:  # client/network errors
                print(f"clickhouse: FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
                return 3
            print(json.dumps(out, indent=2))
            return 0 if all(v == 0 for v in out["checks"].values()) else 1
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
