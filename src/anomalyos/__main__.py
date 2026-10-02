"""CLI entry point. Stage 0 exposes a single command: ``doctor``.

Usage: ``python -m anomalyos doctor`` (or ``anomalyos doctor`` once installed).
Exit code 0 = config valid and ClickHouse healthy; 1 = ClickHouse unhealthy;
2 = configuration invalid.
"""

from __future__ import annotations

import argparse
import sys

from anomalyos import __version__
from anomalyos.clickhouse import check_health
from anomalyos.config import ConfigError, load_settings


def _doctor() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config: INVALID — {exc}", file=sys.stderr)
        return 2
    print(f"anomalyos {__version__} · env={settings.env} · seed={settings.seed}")
    report = check_health(settings.clickhouse)
    status = "OK" if report.ok else "FAIL"
    print(f"clickhouse: {status} — {report.detail}")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anomalyos")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="validate config and check ClickHouse connectivity")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    return 2  # unreachable: argparse enforces the choice


if __name__ == "__main__":
    raise SystemExit(main())
