"""The repo layout is the coding harness, not a product runtime."""

from __future__ import annotations

import unittest

from tests.architecture.paths import ROOT

REQUIRED_PATHS = [
    "AGENTS.md",
    "README.md",
    ".gitignore",
    "docs/ARCHITECTURE.md",
    "docs/INVARIANTS.md",
    "docs/DECISIONS.md",
    "docs/PRODUCT.md",
    "docs/TOOLS.md",
    "docs/SKILLS.md",
    "tests/architecture",
    "evals/scenarios",
    "evals/baselines",
    "evals/benchmarks",
    "evals/harness.py",
    "scripts/check_architecture.py",
    "scripts/eval_smoke.py",
    ".github/workflows/harness.yml",
    ".cursor/rules/harness.mdc",
    ".cursor/rules/architecture.mdc",
    ".cursor/rules/testing.mdc",
    ".cursor/rules/no-product.mdc",
]


class TestRequiredPaths(unittest.TestCase):
    def test_required_paths_exist(self) -> None:
        missing = [rel for rel in REQUIRED_PATHS if not (ROOT / rel).exists()]
        self.assertEqual(missing, [], f"missing required harness paths: {missing}")
