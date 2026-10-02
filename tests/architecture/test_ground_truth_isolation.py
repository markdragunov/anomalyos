"""INV-015: only the simulator and the evaluation layer may touch ground truth."""

from __future__ import annotations

import unittest

from tests.architecture.paths import ROOT

SRC = ROOT / "src" / "anomalyos"
ALLOWED_PACKAGES = {"simulation", "evaluation"}
MARKERS = ("_truth", "ground_truth")


class TestGroundTruthIsolation(unittest.TestCase):
    def test_no_ground_truth_outside_allowed_packages(self) -> None:
        hits: list[str] = []
        for path in SRC.rglob("*.py"):
            rel = path.relative_to(SRC)
            if rel.parts[0] in ALLOWED_PACKAGES:
                continue
            text = path.read_text(encoding="utf-8")
            if any(marker in text for marker in MARKERS):
                hits.append(rel.as_posix())
        self.assertEqual(hits, [], f"INV-015: ground truth referenced outside {sorted(ALLOWED_PACKAGES)}: {hits}")

    def test_guard_is_not_vacuous(self) -> None:
        # The simulator does produce ground truth; if this stops being true the allowlist is stale.
        sim = SRC / "simulation"
        self.assertTrue(any("ground_truth" in p.read_text(encoding="utf-8") for p in sim.rglob("*.py")))
