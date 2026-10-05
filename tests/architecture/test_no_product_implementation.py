"""Fail if code appears beyond the current stage or dependencies creep in (ADR-023).

The repo holds the harness and a stage-by-stage runtime. Stage 0-1 exist (config, ClickHouse
health, synthetic world, loader). Anything for a later layer must arrive with its stage brief,
and with an ADR if it adds a dependency or a top-level directory.
"""

from __future__ import annotations

import re
import unittest

from tests.architecture.paths import ROOT

# `src` is allowed since ADR-023; these top-level product directories still are not.
FORBIDDEN_TOP_LEVEL = {
    "app",
    "apps",
    "services",
    "backend",
    "frontend",
    "packages",
    "cmd",
    "internal",
}

# Layers that do not exist yet (Stage 2+). Remove a name here in the same change that
# implements its stage, and record the stage in docs/ARCHITECTURE.md.
FORBIDDEN_FILENAMES = {
    "jev.py",
    "policy_engine.py",
    "investigation_agent.py",
    "clickhouse_client.py",
}

# Packages under src/pulseos that belong to later stages (`events`, `metrics`: Stage 2; `detection`: Stage 3; `cohorts`: Stage 4;
# `jev`, `policy`: Stage 5).
FORBIDDEN_PACKAGES = {
    "incident",
    "investigation",
    "incidents",
    "agent",
    "explanation",
    "api",
}

# Manifests other than pyproject.toml are not allowed (single Python project).
DEPENDENCY_MANIFESTS = {
    "requirements.txt",
    "requirements.in",
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

# ADR-020 (clickhouse-connect) and ADR-011 (pytest, dev only). Extend only together with an ADR.
ALLOWED_RUNTIME_DEPENDENCIES = {"clickhouse-connect"}
ALLOWED_DEV_DEPENDENCIES = {"pytest"}


def _dependency_names(block: str) -> set[str]:
    return {
        re.split(r"[<>=!~\[ ;]", item.strip().strip(",").strip('"'), maxsplit=1)[0].lower()
        for item in re.findall(r'"([^"]+)"', block)
    }


class TestNoProductImplementation(unittest.TestCase):
    def test_no_product_top_level_packages(self) -> None:
        present = sorted(name for name in FORBIDDEN_TOP_LEVEL if (ROOT / name).exists())
        self.assertEqual(present, [], f"product directories present too early: {present}")

    def test_no_later_stage_packages(self) -> None:
        base = ROOT / "src" / "pulseos"
        present = sorted(name for name in FORBIDDEN_PACKAGES if (base / name).exists())
        self.assertEqual(present, [], f"packages for later stages present too early: {present}")

    def test_no_product_module_filenames(self) -> None:
        hits: list[str] = []
        for path in ROOT.rglob("*"):
            if ".git" in path.parts or ".venv" in path.parts or "evals/output" in path.as_posix():
                continue
            if path.name in FORBIDDEN_FILENAMES:
                hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [], f"product modules present too early: {hits}")

    def test_no_other_dependency_manifests(self) -> None:
        present = sorted(name for name in DEPENDENCY_MANIFESTS if (ROOT / name).exists())
        self.assertEqual(present, [], f"only pyproject.toml may declare dependencies: {present}")

    def test_pyproject_dependency_allowlist(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        runtime = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
        dev = re.search(r"^dev\s*=\s*\[(.*?)\]", text, re.S | re.M)
        self.assertIsNotNone(runtime, "pyproject.toml must declare runtime dependencies")
        self.assertEqual(
            _dependency_names(runtime.group(1)),
            ALLOWED_RUNTIME_DEPENDENCIES,
            "runtime dependencies changed: record an ADR in docs/DECISIONS.md and update this allowlist",
        )
        self.assertIsNotNone(dev, "pyproject.toml must declare dev dependencies")
        self.assertEqual(
            _dependency_names(dev.group(1)),
            ALLOWED_DEV_DEPENDENCIES,
            "dev dependencies changed: record an ADR in docs/DECISIONS.md and update this allowlist",
        )
