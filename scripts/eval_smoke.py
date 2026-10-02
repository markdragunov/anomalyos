#!/usr/bin/env python3
"""Evaluation smoke: validate scenarios and write decision traces."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.harness import ScenarioError, run_smoke  # noqa: E402


def main() -> int:
    try:
        traces = run_smoke()
    except ScenarioError as exc:
        print(f"eval smoke failed: {exc}", file=sys.stderr)
        return 1
    print(f"eval smoke ok: {len(traces)} trace(s)")
    print(json.dumps({"scenarios": [t["scenario_id"] for t in traces]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
