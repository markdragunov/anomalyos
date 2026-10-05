"""Seed sets for development and held-out evaluation (docs/specs/10_EVALUATION_BENCHMARK.md).

Thresholds, question sets, prompts and every other tunable are chosen on ``DEV_SEEDS`` only and frozen
before ``HELDOUT_SEEDS`` are touched. The benchmark reports on the held-out seeds; reporting on the dev
seeds, or re-tuning after looking at held-out results, invalidates it.

``DEMO_SEED`` (42) is used by documentation and the golden digests; it belongs to neither set.
Invariants: the two sets are disjoint and fixed (changing them is a deliberate, documented decision).
"""

from __future__ import annotations

DEV_SEEDS: tuple[int, ...] = tuple(range(1, 21))
HELDOUT_SEEDS: tuple[int, ...] = tuple(range(1001, 1021))
DEMO_SEED = 42
