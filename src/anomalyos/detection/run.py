"""Run detection for one world: query each monitored series once (first-look visibility) and scan it.

Input: a ``QueryRunner`` (ClickHouse), database, run id, world start/end, config, system ("main" or "static").
Output: prefiltered ``AnomalyCandidate``s, sorted by detection time. ClickHouse is reached only through
``metrics.compute`` (INV-003); ground truth is never read (INV-015).
Replay: series are computed with ``visibility="window_close"`` (each window sees only what had been delivered when
it closed) and scanned sequentially, which is equivalent to deciding at every window close (ADR-034 D-6).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from anomalyos.detection.candidates import AnomalyCandidate, prefilter
from anomalyos.detection.config import SERIES, DetectorConfig, SeriesSpec
from anomalyos.detection.detectors import scan_main, scan_static
from anomalyos.metrics import GRAINS, compute

SYSTEMS = {"main": scan_main, "static": scan_static}


def detect(runner, database: str, run_id: str, start: datetime, end: datetime, *, cfg: DetectorConfig = DetectorConfig(),
           system: str = "main", series: tuple[SeriesSpec, ...] = SERIES) -> list[AnomalyCandidate]:
    if system not in SYSTEMS:
        raise ValueError(f"unknown detection system {system!r}")
    scan = SYSTEMS[system]
    world_start = int(start.timestamp())
    cands: list[AnomalyCandidate] = []
    for spec in series:
        points = compute(runner, database, run_id, spec.metric, spec.version, start, end, spec.grain, None,
                         spec.group_by, visibility="window_close")
        groups: dict[tuple, list[tuple[int, int, int]]] = defaultdict(list)
        for p in points:
            if p.is_complete:
                groups[p.dims].append((int(p.window_start.timestamp()), p.numerator, p.denominator))
        for scope, windows in sorted(groups.items()):
            cands += scan(spec, cfg, windows, world_start, GRAINS[spec.grain][2], scope)
    return prefilter(cands, cfg.min_sample)
