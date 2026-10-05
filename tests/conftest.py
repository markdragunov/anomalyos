"""Opt-in switches shared by all product tests."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """`slow` tests run only with PULSEOS_RUN_SLOW=1 (skipped with a stated reason otherwise)."""
    if os.environ.get("PULSEOS_RUN_SLOW") == "1":
        return
    skip = pytest.mark.skip(reason="slow: set PULSEOS_RUN_SLOW=1 (full-scale seed 42 generation, about a minute)")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
