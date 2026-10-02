"""CI must run real harness commands, not placeholders."""

from __future__ import annotations

import unittest

from tests.architecture.paths import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "harness.yml"

REQUIRED_SNIPPETS = [
    "python -m unittest discover -s tests/architecture -t . -v",
    "python scripts/check_architecture.py",
    "python scripts/eval_smoke.py",
    "actions/setup-python",
    "python-version:",
]


class TestCiWorkflow(unittest.TestCase):
    def test_workflow_exists(self) -> None:
        self.assertTrue(WORKFLOW.is_file())

    def test_workflow_invokes_real_commands(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        missing = [snippet for snippet in REQUIRED_SNIPPETS if snippet not in text]
        self.assertEqual(missing, [], f"workflow missing real commands: {missing}")

    def test_workflow_has_no_fake_success_jobs(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for fake in ("exit 0", "echo skip", "echo skipped", "true  # lint"):
            self.assertNotIn(fake, text)

    def test_workflow_does_not_call_missing_linters(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("ruff", text)
        self.assertNotIn("mypy", text)
        self.assertNotIn("pytest", text)
