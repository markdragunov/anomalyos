"""Shared, module-cached simulation runs for Stage 1 tests (seeded; no network, no wall clock)."""

from __future__ import annotations

import json

import pytest

from pulseos.simulation.runner import generate
from pulseos.simulation.world import WorldConfig

SMALL = dict(seed=1234, scale=0.08)


@pytest.fixture(scope="session")
def full_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("full_run")
    events: list[str] = []
    res = generate(WorldConfig(**SMALL), "full", out, on_event=lambda ev, line: events.append(line))
    return {"res": res, "dir": out, "lines": events, "events": [json.loads(x) for x in events]}
