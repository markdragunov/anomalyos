"""Eval scenarios must carry explicit ground truth (INV-014)."""

from __future__ import annotations

import unittest

from evals.harness import load_scenarios, validate_scenario


class TestEvalGroundTruth(unittest.TestCase):
    def test_repo_scenarios_load(self) -> None:
        loaded = load_scenarios()
        self.assertGreaterEqual(len(loaded), 1)
        for path, data in loaded:
            with self.subTest(path=path.name):
                self.assertIn("disposition", data["ground_truth"])

    def test_missing_ground_truth_is_rejected(self) -> None:
        payload = {
            "id": "BAD-001",
            "title": "broken",
            "purpose": "negative fixture",
            "mode": "A",
            "inputs": {"signals": {}},
            "budgets": {"max_tool_calls": 0, "max_tokens": 0, "max_latency_ms": 1000},
            "replay": {"deterministic": True},
        }
        with self.assertRaisesRegex(ValueError, "missing keys"):
            validate_scenario(payload, source="memory")

    def test_raw_events_in_inputs_are_rejected(self) -> None:
        payload = {
            "id": "BAD-002",
            "title": "raw events",
            "purpose": "INV-001",
            "mode": "A",
            "inputs": {"events": [{"id": "evt-1"}]},
            "ground_truth": {"disposition": "IGNORE", "evidence_ids": []},
            "budgets": {"max_tool_calls": 0, "max_tokens": 0, "max_latency_ms": 1000},
            "replay": {"deterministic": True},
        }
        with self.assertRaisesRegex(ValueError, "INV-001"):
            validate_scenario(payload, source="memory")
