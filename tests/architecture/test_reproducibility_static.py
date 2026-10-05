"""INV-016: the simulator must not use wall clock, global randomness, hash() or uuid4."""

from __future__ import annotations

import ast
import unittest

from tests.architecture.paths import ROOT

SIM = ROOT / "src" / "pulseos" / "simulation"

FORBIDDEN_BARE = {"hash"}
FORBIDDEN_ATTRS = {
    ("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"), ("date", "today"),
    ("time", "time"), ("time", "monotonic"), ("time", "perf_counter"), ("time", "time_ns"),
    ("uuid", "uuid4"), ("uuid", "uuid1"),
    ("random", "random"), ("random", "randint"), ("random", "randrange"), ("random", "choice"),
    ("random", "choices"), ("random", "shuffle"), ("random", "uniform"), ("random", "sample"),
    ("random", "gauss"), ("random", "seed"),
}


def _violations(path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_BARE:
            out.append(f"{path.name}:{node.lineno} {fn.id}()")
        elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and (fn.value.id, fn.attr) in FORBIDDEN_ATTRS:
            out.append(f"{path.name}:{node.lineno} {fn.value.id}.{fn.attr}()")
    return out


class TestReproducibilityStatic(unittest.TestCase):
    def test_no_nondeterministic_calls_in_simulator(self) -> None:
        hits = [v for p in sorted(SIM.rglob("*.py")) for v in _violations(p)]
        self.assertEqual(hits, [], f"INV-016: nondeterministic calls in simulator: {hits}")

    def test_checker_detects_violations(self) -> None:
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "bad.py"
            p.write_text("import time, random\nx = hash('a')\ny = time.time()\nz = random.random()\n")
            self.assertEqual(len(_violations(p)), 3)
