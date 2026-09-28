"""Fail if product runtime or unnecessary frameworks appear during harness-only work."""

from __future__ import annotations

import unittest

from tests.architecture.paths import ROOT

FORBIDDEN_TOP_LEVEL = {
    "src",
    "app",
    "apps",
    "services",
    "backend",
    "frontend",
    "packages",
    "cmd",
    "internal",
}

FORBIDDEN_FILENAMES = {
    "jev.py",
    "policy_engine.py",
    "investigation_agent.py",
    "anomaly_detector.py",
    "detector.py",
    "clickhouse_client.py",
}

DEPENDENCY_MANIFESTS = {
    "requirements.txt",
    "requirements.in",
    "pyproject.toml",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "go.mod",
    "Cargo.toml",
    "composer.json",
}

ALLOWED_DOT_CURSOR = {"rules"}


class TestNoProductImplementation(unittest.TestCase):
    def test_no_product_top_level_packages(self) -> None:
        present = sorted(
            name
            for name in FORBIDDEN_TOP_LEVEL
            if (ROOT / name).exists()
        )
        self.assertEqual(present, [], f"product packages present too early: {present}")

    def test_no_product_module_filenames(self) -> None:
        hits: list[str] = []
        for path in ROOT.rglob("*"):
            if ".git" in path.parts or "evals/output" in path.as_posix():
                continue
            if path.name in FORBIDDEN_FILENAMES:
                hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [], f"product modules present too early: {hits}")

    def test_no_dependency_manifests(self) -> None:
        present = sorted(name for name in DEPENDENCY_MANIFESTS if (ROOT / name).exists())
        self.assertEqual(
            present,
            [],
            f"dependency manifests not allowed in harness bootstrap (ADR-002): {present}",
        )

    def test_no_cursor_skill_stubs(self) -> None:
        skills = ROOT / ".cursor" / "skills"
        self.assertFalse(
            skills.exists(),
            "Do not stub .cursor/skills until a real procedure exists (ADR-004)",
        )

    def test_cursor_rules_are_mdc_only(self) -> None:
        rules = ROOT / ".cursor" / "rules"
        self.assertTrue(rules.is_dir())
        extra = sorted(
            path.name
            for path in rules.iterdir()
            if path.suffix != ".mdc"
        )
        self.assertEqual(extra, [], f"non-mdc files in .cursor/rules: {extra}")
