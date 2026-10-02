"""Invariant catalog in docs/INVARIANTS.md must stay complete and numbered."""

from __future__ import annotations

import re
import unittest

from tests.architecture.paths import ROOT

REQUIRED_INVARIANTS = {
    "INV-001": "No Raw Event → AI",
    "INV-002": "No AI → Arbitrary SQL",
    "INV-003": "Approved Analytical Interfaces",
    "INV-004": "Jev Is Side-Effect Free",
    "INV-005": "Jev Cannot Execute Tools",
    "INV-006": "Policy Is Deterministic",
    "INV-007": "Agent Is Read-Only in V2",
    "INV-008": "LLM Uses Validated Evidence",
    "INV-009": "Evidence Is Traceable",
    "INV-010": "Decisions Are Auditable",
    "INV-011": "Agent Budgets Are Bounded",
    "INV-012": "No Autonomous Irreversible Actions",
}

HEADING_RE = re.compile(
    r"^## (INV-\d{3}) — (.+)$",
    re.MULTILINE,
)

SECTIONS = ("What it means", "Why it exists", "How it could be violated", "How it could be tested")


class TestInvariantCatalog(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROOT / "docs" / "INVARIANTS.md").read_text(encoding="utf-8")

    def test_required_invariant_ids_present(self) -> None:
        headings = dict(HEADING_RE.findall(self.text))
        missing = [inv_id for inv_id in REQUIRED_INVARIANTS if inv_id not in headings]
        self.assertEqual(missing, [], f"missing invariant headings: {missing}")

    def test_required_titles_match(self) -> None:
        headings = dict(HEADING_RE.findall(self.text))
        for inv_id, title in REQUIRED_INVARIANTS.items():
            self.assertEqual(
                headings.get(inv_id),
                title,
                f"{inv_id} title drifted (expected {title!r})",
            )

    def test_each_required_invariant_has_contract_sections(self) -> None:
        chunks = re.split(r"\n## ", self.text)
        by_id: dict[str, str] = {}
        for chunk in chunks:
            match = re.match(r"(INV-\d{3}) — ", chunk)
            if match:
                by_id[match.group(1)] = chunk
        for inv_id in REQUIRED_INVARIANTS:
            body = by_id.get(inv_id, "")
            for section in SECTIONS:
                self.assertIn(
                    f"**{section}.**",
                    body,
                    f"{inv_id} missing section {section}",
                )


class TestOperatingRulesMapping(unittest.TestCase):
    def test_agents_rules_16_to_19_are_mapped(self) -> None:
        text = (ROOT / "docs" / "INVARIANTS.md").read_text(encoding="utf-8")
        self.assertIn("## Operating rules that are not numbered invariants", text)
        for rule in ("16 — Ground truth is isolated", "17 — Seeded and reproducible",
                     "18 — Data conventions", "19 — Jev answers typed questions only"):
            self.assertIn(rule, text)
