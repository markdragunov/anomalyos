"""INV-004/005/006/015 for the decision layer and incident engine (ADR-024, ADR-039, ADR-041), checked with ``ast``.

* network I/O only in ``jev/transport.py``; ``policy`` does none (its audit writer receives a client object);
* no clock reads anywhere in ``jev``, ``policy`` or ``incidents`` (time is an explicit input, ADR-039 D-2, ADR-041 D-0);
* none of them imports the simulator or the evaluation layer (ground truth stays out of reach).
"""

from __future__ import annotations

import ast
import unittest

from tests.architecture.paths import ROOT

SRC = ROOT / "src" / "pulseos"
NETWORK_MODULES = {"socket", "http", "urllib", "requests", "httpx", "aiohttp", "clickhouse_connect", "subprocess", "ssl"}
FORBIDDEN_IMPORTS = {"pulseos.simulation", "pulseos.evaluation"}
CLOCK_CALLS = {("time", "time"), ("time", "monotonic"), ("datetime", "now"), ("datetime", "utcnow"), ("date", "today")}


def _modules(package: str):
    base = SRC / package
    return sorted(base.rglob("*.py")) if base.exists() else []


def _imports(tree: ast.AST) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


class TestDecisionLayer(unittest.TestCase):
    def test_packages_exist(self) -> None:
        self.assertTrue(_modules("jev"), "src/pulseos/jev is the Stage 5 decision layer")

    def test_network_io_only_in_the_transport_module(self) -> None:
        hits = []
        for pkg in ("jev", "policy", "investigation", "explanation", "context"):
            for path in _modules(pkg):
                if pkg == "jev" and path.name == "transport.py":
                    continue
                mods = _imports(ast.parse(path.read_text(encoding="utf-8")))
                bad = sorted(m for m in mods if m.split(".")[0] in NETWORK_MODULES)
                if bad:
                    hits.append(f"{path.relative_to(ROOT)}: {bad}")
        self.assertEqual(hits, [], f"INV-004: network I/O outside jev/transport.py: {hits}")

    def test_no_clock_reads(self) -> None:
        hits = []
        for pkg in ("jev", "policy", "incidents", "investigation", "explanation", "context"):
            for path in _modules(pkg):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                            and isinstance(node.func.value, ast.Name) and (node.func.value.id, node.func.attr) in CLOCK_CALLS:
                        hits.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(hits, [], f"ADR-039 D-2: clock read in the decision layer: {hits}")

    def test_no_simulator_or_evaluation_imports(self) -> None:
        hits = []
        for pkg in ("jev", "policy", "incidents", "investigation", "explanation", "context"):
            for path in _modules(pkg):
                mods = _imports(ast.parse(path.read_text(encoding="utf-8")))
                bad = sorted(m for m in mods if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_IMPORTS))
                if bad:
                    hits.append(f"{path.relative_to(ROOT)}: {bad}")
        self.assertEqual(hits, [], f"INV-015: decision layer imports simulator / evaluation: {hits}")


class TestInvestigationTools(unittest.TestCase):
    """INV-007: the agent's tool registry holds only read-only tools (ADR-049 D-3)."""

    MUTATION = ("refund", "retry", "capture", "cancel", "disable", "page", "notify", "write", "update", "delete",
                "close", "resolve")

    def _registry(self) -> list[str]:
        tree = ast.parse((SRC / "investigation" / "tools.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
            if isinstance(target, ast.Name) and target.id == "REGISTRY" and isinstance(node.value, ast.Dict):
                return [k.value for k in node.value.keys]
        self.fail("investigation/tools.py has no REGISTRY dict")

    def test_no_mutation_tool(self) -> None:
        names = self._registry()
        self.assertEqual(len(names), 10)
        bad = [n for n in names if any(m in n.lower() for m in self.MUTATION)]
        self.assertEqual(bad, [], f"INV-007: mutation-like tool names in the investigation registry: {bad}")


if __name__ == "__main__":
    unittest.main()
