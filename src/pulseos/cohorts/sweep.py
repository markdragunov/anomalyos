"""Daily cohort sweep (ADR-037, design C-8): finds drops hidden from the global and per-PSP series.

For every complete day after warm-up, ``authorization_rate`` on the sweep combinations, day d against days
d-8 ... d-2 (pooled, the most extreme day dropped), one-sided down, Benjamini-Hochberg at ``sweep_q`` across all of
the day's supported cohorts. Discoveries become ``S12`` candidates (consecutive days of the same cohort merged).
One query per combination for the whole world. Never reads ground truth.
Off by default since Gate 1 (ADR-038: 0.29 false positives per day on realism v2 for +2..6 detections of ~330); it
runs only with ``CohortConfig(sweep_enabled=True)``, for reports.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pulseos.cohorts.analysis import benjamini_hochberg, norm_sf, phi_from_reference_days, z_rate
from pulseos.cohorts.config import SWEEP_COMBINATIONS, CohortConfig
from pulseos.cohorts.fetch import DAY, pool
from pulseos.detection.candidates import AnomalyCandidate, evidence_id, stable_hash
from pulseos.metrics import compute

METRIC, VERSION = "authorization_rate", 1


def sweep(runner, database: str, run_id: str, start: datetime, end: datetime, cfg: CohortConfig = CohortConfig(),
          warmup_days: int = 3) -> list[AnomalyCandidate]:
    if not cfg.sweep_enabled:
        return []
    w0, w1 = int(start.timestamp()), int(end.timestamp())
    series = {combo: compute(runner, database, run_id, METRIC, VERSION, start, end, "1d", None, combo,
                             dense=False, visibility="window_close") for combo in SWEEP_COMBINATIONS}
    hits: dict[tuple, list[tuple[int, float, float, float, float]]] = {}
    for day in range(w0 + warmup_days * DAY, w1, DAY):
        tested = []
        for combo, points in series.items():
            rows = pool(points, {}, day, day + DAY, cfg.ref_lag_days, w0, "rate")
            phi = phi_from_reference_days(list(rows.values()), "rate", cfg.min_support)
            for r in rows.values():
                if r.n_b < cfg.min_support or r.n_d < cfg.min_support:
                    continue
                z = z_rate(r.k_b, r.n_b, r.k_d, r.n_d, phi)
                if z is not None:
                    tested.append((tuple(sorted(r.dims)), z, r))
        for (dims, z, r), keep in zip(tested, benjamini_hochberg([norm_sf(-z) for _, z, _ in tested], cfg.sweep_q)):
            if keep:
                hits.setdefault(dims, []).append((day, z, r.k_d / r.n_d, r.k_b / r.n_b, r.n_d))
    out = []
    for dims, days in sorted(hits.items()):
        runs, cur = [], [days[0]]
        for d in days[1:]:
            if d[0] == cur[-1][0] + DAY:
                cur.append(d)
            else:
                runs.append(cur)
                cur = [d]
        runs.append(cur)
        for run in runs:
            first = run[0]
            observed, expected = first[2], first[3]
            out.append(AnomalyCandidate(
                anomaly_id="anom_" + stable_hash("main", "S12", METRIC, VERSION, dims, first[0]), system="main",
                series_key="S12", metric=METRIC, metric_version=VERSION, grain="1d", scope=dims, direction="down",
                window_start=first[0], window_end=run[-1][0] + DAY, detected_at=first[0] + DAY,
                observed=round(observed, 6), expected=round(expected, 6), delta=round(observed - expected, 6),
                relative_delta=round((observed - expected) / expected, 6) if expected else None,
                score=round(max(abs(x[1]) for x in run), 3), sample_size=int(first[4]), method=("cohort_sweep",),
                evidence_ids=tuple(evidence_id(METRIC, VERSION, dims, "1d", x[0]) for x in run[:48])))
    return sorted(out, key=lambda c: (c.detected_at, c.scope))
