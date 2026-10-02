"""Cursor project rules stay focused and do not clone AGENTS.md."""

from __future__ import annotations

import unittest

from tests.architecture.paths import ROOT


class TestCursorRules(unittest.TestCase):
    def test_no_legacy_cursorrules(self) -> None:
        self.assertFalse((ROOT / ".cursorrules").exists())

    def test_claude_md_is_a_thin_pointer(self) -> None:
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 15, "CLAUDE.md must not duplicate AGENTS.md (ADR-003)")
        self.assertIn("AGENTS.md", text)

    def test_always_apply_rule_is_short(self) -> None:
        text = (ROOT / ".cursor" / "rules" / "harness.mdc").read_text(encoding="utf-8")
        self.assertIn("alwaysApply: true", text)
        self.assertLess(len(text.splitlines()), 40)
        self.assertNotIn("INV-001", text)

    def test_rules_point_at_agents_md(self) -> None:
        text = (ROOT / ".cursor" / "rules" / "harness.mdc").read_text(encoding="utf-8")
        self.assertIn("AGENTS.md", text)
