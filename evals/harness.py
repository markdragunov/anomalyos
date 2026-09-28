"""Evaluation harness: load, validate, smoke-run.

Stdlib only. Does not detect anomalies, call Jev, or talk to ClickHouse.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = ROOT / "evals" / "scenarios"
OUTPUT_DIR = ROOT / "evals" / "output"

REQUIRED_SCENARIO_KEYS = (
    "id",
    "title",
    "purpose",
    "mode",
    "inputs",
    "ground_truth",
    "budgets",
    "replay",
)

REQUIRED_BUDGET_KEYS = ("max_tool_calls", "max_tokens", "max_latency_ms")
ALLOWED_MODES = {"A", "B"}
ALLOWED_DISPOSITIONS = {"IGNORE", "DIGEST", "INCIDENT"}


class ScenarioError(ValueError):
    """A scenario file violates the evaluation contract."""


def scenario_paths() -> list[Path]:
    return sorted(SCENARIOS_DIR.glob("*.json"))


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{path}: invalid JSON ({exc})") from exc


def validate_scenario(data: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ScenarioError(f"{source}: scenario must be a JSON object")

    missing = [key for key in REQUIRED_SCENARIO_KEYS if key not in data]
    if missing:
        raise ScenarioError(f"{source}: missing keys {missing}")

    if not isinstance(data["ground_truth"], dict) or not data["ground_truth"]:
        raise ScenarioError(f"{source}: ground_truth must be a non-empty object")

    disposition = data["ground_truth"].get("disposition")
    if disposition not in ALLOWED_DISPOSITIONS:
        raise ScenarioError(
            f"{source}: ground_truth.disposition must be one of {sorted(ALLOWED_DISPOSITIONS)}"
        )

    if data["mode"] not in ALLOWED_MODES:
        raise ScenarioError(f"{source}: mode must be 'A' or 'B'")

    if not isinstance(data["inputs"], dict):
        raise ScenarioError(f"{source}: inputs must be an object (bounded, not a raw list of events)")

    if "events" in data["inputs"]:
        raise ScenarioError(f"{source}: inputs.events is forbidden (INV-001 No Raw Event → AI)")

    budgets = data["budgets"]
    if not isinstance(budgets, dict):
        raise ScenarioError(f"{source}: budgets must be an object")
    missing_budgets = [key for key in REQUIRED_BUDGET_KEYS if key not in budgets]
    if missing_budgets:
        raise ScenarioError(f"{source}: budgets missing {missing_budgets}")
    for key in REQUIRED_BUDGET_KEYS:
        if not isinstance(budgets[key], int) or budgets[key] < 0:
            raise ScenarioError(f"{source}: budgets.{key} must be an int >= 0")

    replay = data["replay"]
    if not isinstance(replay, dict) or replay.get("deterministic") is not True:
        raise ScenarioError(f"{source}: replay.deterministic must be true")

    evidence_ids = data["ground_truth"].get("evidence_ids", [])
    if not isinstance(evidence_ids, list):
        raise ScenarioError(f"{source}: ground_truth.evidence_ids must be a list")

    return data


def load_scenarios() -> list[tuple[Path, dict[str, Any]]]:
    paths = scenario_paths()
    if not paths:
        raise ScenarioError(f"no scenario JSON files in {SCENARIOS_DIR}")
    loaded: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        data = validate_scenario(load_json(path), source=str(path.relative_to(ROOT)))
        loaded.append((path, data))
    ids = [data["id"] for _, data in loaded]
    if len(ids) != len(set(ids)):
        raise ScenarioError(f"duplicate scenario ids: {ids}")
    return loaded


def smoke_trace(scenario: dict[str, Any], *, latency_ms: float) -> dict[str, Any]:
    """Build a decision-trace document. Disposition comes from ground truth until policy exists."""
    ground = scenario["ground_truth"]
    return {
        "scenario_id": scenario["id"],
        "mode": scenario["mode"],
        "disposition": ground["disposition"],
        "evidence_ids": list(ground.get("evidence_ids", [])),
        "policy_version": None,
        "budgets": scenario["budgets"],
        "cost": {"tokens": 0, "usd": 0.0},
        "latency_ms": round(latency_ms, 3),
        "replay": scenario["replay"],
        "notes": "harness smoke: no detector, Jev, or LLM was invoked",
    }


def run_smoke(output_dir: Path | None = None) -> list[dict[str, Any]]:
    started = time.perf_counter()
    scenarios = load_scenarios()
    traces: list[dict[str, Any]] = []
    dest = output_dir or OUTPUT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    for path, scenario in scenarios:
        elapsed_ms = (time.perf_counter() - started) * 1000
        trace = smoke_trace(scenario, latency_ms=elapsed_ms)
        traces.append(trace)
        out = dest / f"{scenario['id']}.trace.json"
        out.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
        if elapsed_ms > scenario["budgets"]["max_latency_ms"]:
            raise ScenarioError(
                f"{path.name}: smoke latency {elapsed_ms:.1f}ms exceeded "
                f"max_latency_ms={scenario['budgets']['max_latency_ms']}"
            )
    return traces
