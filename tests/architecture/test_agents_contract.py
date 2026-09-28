"""AGENTS.md must state the binding rules and workflow."""

from __future__ import annotations

import unittest

from tests.architecture.paths import ROOT

REQUIRED_PHRASES = [
    "ClickHouse will be the analytical source of truth",
    "AI must never receive unbounded raw event streams",
    "AI must never generate arbitrary SQL",
    "AI accesses analytical data through approved typed interfaces",
    "Jev is side-effect free",
    "Jev cannot execute tools",
    "Policy is deterministic",
    "Investigation Agent is read-only in V2",
    "LLM explanations may only reference validated evidence",
    "Important AI decisions must be auditable",
    "Evidence must have stable identifiers",
    "Observed, inferred, estimated, and recommended",
    "Agent execution must have explicit budgets",
    "No autonomous irreversible actions in V2",
    "Evaluation scenarios must contain explicit ground truth",
    "READ → PLAN → IMPLEMENT → TEST → ARCHITECTURE CHECK → EVALUATE → REVIEW → COMMIT",
    "must not silently violate an invariant",
    "Jev decides. Code routes. LLM explains. Agent investigates.",
]


class TestAgentsContract(unittest.TestCase):
    def test_agents_md_states_required_rules(self) -> None:
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        missing = [phrase for phrase in REQUIRED_PHRASES if phrase not in text]
        self.assertEqual(missing, [], f"AGENTS.md missing required phrases: {missing}")
