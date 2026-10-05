"""CLI entry point: ``doctor`` (Stage 0) and ``normalize`` (Stage 2).

Usage: ``pulseos doctor`` · ``pulseos normalize --in data/run_42 [--replace]``.
Exit codes: 0 ok; 1 ClickHouse unhealthy / checks failed; 2 configuration or usage invalid; 3 ClickHouse error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pulseos import __version__
from pulseos.clickhouse import check_health
from pulseos.config import ConfigError, load_settings


def _doctor() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config: INVALID — {exc}", file=sys.stderr)
        return 2
    print(f"pulseos {__version__} · env={settings.env} · seed={settings.seed}")
    report = check_health(settings.clickhouse)
    status = "OK" if report.ok else "FAIL"
    print(f"clickhouse: {status} — {report.detail}")
    return 0 if report.ok else 1


def _normalize(run_dir: Path, replace: bool) -> int:
    from pulseos.events.normalize import NormalizationError
    from pulseos.events.store import load_norm
    from pulseos.simulation.clickhouse_load import connect, target_from_settings

    try:
        target = target_from_settings()
    except ConfigError as exc:
        print(f"config: INVALID — {exc}", file=sys.stderr)
        return 2
    try:
        out = load_norm(connect(target), run_dir, target.database, replace=replace)
    except NormalizationError as exc:
        print(f"normalization error: {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # clickhouse_connect raises its own hierarchy
        print(f"clickhouse error: {exc}", file=sys.stderr)
        return 3
    bad = {k: v for k, v in out["checks"].items() if v}
    print(f"{out['run_id']}: {out['norm_rows']:,} normalized rows from {out['raw_events']:,} raw events "
          f"(normalization {out['normalization_version']})")
    for name, value in out["checks"].items():
        print(f"  check {name}: {'OK' if not value else f'FAIL ({value})'}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pulseos")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="validate config and check ClickHouse connectivity")
    nz = sub.add_parser("normalize", help="build events_norm for a run already loaded with pulseos-sim load")
    nz.add_argument("--in", dest="inp", type=Path, required=True)
    nz.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    if args.command == "normalize":
        return _normalize(args.inp, args.replace)
    return 2  # unreachable: argparse enforces the choice


if __name__ == "__main__":
    raise SystemExit(main())
