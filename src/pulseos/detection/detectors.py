"""Baseline and detectors over one dense series (one metric, one scope). Pure functions, no I/O.

Why: "what changed?" must be answered deterministically and reproducibly, from past data only.
Input: windows ``(start, numerator, denominator)`` of one group in time order (complete windows only), the
``SeriesSpec``, ``DetectorConfig``, the world start, the grain length. Output: ``AnomalyCandidate``s.
Invariants
* Processing is strictly sequential: the decision for window *w* uses windows before *w* and *w* itself, so
  truncating the series never changes earlier decisions (no future leakage; tested).
* Baseline (ADR-034 D-2): same slot of days d-8 .. d-2, most extreme reference dropped when >= 4 remain.
* z is computed on variance-stabilized scales (arcsine / Anscombe) and is overdispersion-aware: phi from the
  robust spread of recent unit-variance z, floored at 1.
* Gates: min sample (rates), min expected count (counts), min excess events.
Failure modes: thin or missing baseline -> no decision for that window (never a false alarm by default).
"""

from __future__ import annotations

import math
from collections import deque
from statistics import median
from typing import Iterable, Sequence

from pulseos.detection.candidates import AnomalyCandidate, evidence_id, stable_hash
from pulseos.detection.config import DetectorConfig, SeriesSpec

DAY = 86_400
Window = tuple[int, int, int]  # (window start epoch, numerator, denominator)


def _value(spec: SeriesSpec, num: int, den: int) -> float | None:
    if spec.kind == "count":
        return float(num)
    return num / den if den else None


def baseline(spec: SeriesSpec, cfg: DetectorConfig, index: dict[int, tuple[int, int]], ts: int) -> dict | None:
    refs = []
    for k in cfg.ref_lag_days:
        w = index.get(ts - k * DAY)
        if w is None:
            continue
        if spec.kind == "rate" and w[1] <= 0:
            continue
        refs.append(w)
    if len(refs) < cfg.min_ref_windows:
        return None
    if len(refs) >= cfg.trim_when_at_least:
        vals = [_value(spec, n, d) for n, d in refs]
        mid = median(vals)
        drop = max(range(len(refs)), key=lambda i: (abs(vals[i] - mid), i))
        refs = [r for i, r in enumerate(refs) if i != drop]
    if spec.kind == "count":
        return {"mu": sum(n for n, _ in refs) / len(refs), "m": len(refs)}
    n_ref = sum(d for _, d in refs)
    return {"p": sum(n for n, _ in refs) / n_ref, "n_ref": n_ref} if n_ref else None


def unit_z(spec: SeriesSpec, cfg: DetectorConfig, num: int, den: int, base: dict) -> tuple[float, float, float] | None:
    """(z with phi = 1, expected value, excess events) or None when the window is not decidable."""
    # Variance-stabilizing transforms (arcsine for proportions, Anscombe for counts): the plain normal
    # approximation has a left tail ~10x too heavy at approval ~0.9 (binomial skew), which pages on noise.
    # The second variance term accounts for the estimated baseline (n / n_ref, 1 / m).
    if spec.kind == "count":
        mu, m = base["mu"], base["m"]
        if mu < cfg.min_expected_count:
            return None
        z = 2 * (math.sqrt(num + 0.375) - math.sqrt(mu + 0.375)) / math.sqrt(1 + 1 / m)
        return z, mu, num - mu
    if den < cfg.min_sample:
        return None
    p, n_ref = base["p"], base["n_ref"]
    # Ratio metrics are not strict proportions (e.g. cancellations / renewal attempts in a window can exceed 1):
    # the transform is applied to values clamped to [0, 1]; expected value and excess stay unclamped.
    x, q = min(1.0, num / den), min(1.0, p)
    z = 2 * math.sqrt(den) * (math.asin(math.sqrt(x)) - math.asin(math.sqrt(q))) / math.sqrt(1 + den / n_ref)
    return z, p, num - den * p


def robust_phi(history: Sequence[float], cfg: DetectorConfig) -> float:
    if len(history) < cfg.phi_min_history:
        return cfg.phi_default
    return max(1.0, (median(abs(z) for z in history) / 0.6745) ** 2)


def _oriented(spec: SeriesSpec, z: float) -> tuple[float, str]:
    if spec.direction == "down":
        return -z, "down"
    if spec.direction == "up":
        return z, "up"
    return abs(z), ("up" if z > 0 else "down")


class _Open:
    def __init__(self, ts, end, detected_at, direction, observed, expected, score, sample, methods, ev):
        self.first, self.end, self.detected_at, self.direction = ts, end, detected_at, direction
        self.observed, self.expected, self.score, self.sample = observed, expected, score, sample
        self.methods, self.evidence, self.quiet = set(methods), [ev], 0
        self.first_score, self.first_methods = score, tuple(sorted(methods))  # known at detected_at (ADR-039 D-0)


def _close(o: _Open, system: str, spec: SeriesSpec, scope, recovered_at: int | None) -> AnomalyCandidate:
    delta = o.observed - o.expected
    return AnomalyCandidate(
        anomaly_id="anom_" + stable_hash(system, spec.key, spec.metric, spec.version, scope, o.first),
        system=system, series_key=spec.key, metric=spec.metric, metric_version=spec.version, grain=spec.grain,
        scope=scope, direction=o.direction, window_start=o.first, window_end=o.end, detected_at=o.detected_at,
        observed=round(o.observed, 6), expected=round(o.expected, 6), delta=round(delta, 6),
        relative_delta=round(delta / o.expected, 6) if o.expected else None, score=round(o.score, 3),
        sample_size=o.sample, method=tuple(sorted(o.methods)), evidence_ids=tuple(o.evidence[:48]),
        status="recovered" if recovered_at is not None else "promoted", recovered_at=recovered_at,
        score_at_detection=round(o.first_score, 3), methods_at_detection=o.first_methods)


def scan_main(spec: SeriesSpec, cfg: DetectorConfig, windows: Iterable[Window], world_start: int, grain_s: int,
              scope: tuple[tuple[str, str], ...]) -> list[AnomalyCandidate]:
    windows = list(windows)
    index = {ts: (n, d) for ts, n, d in windows}
    history: deque[float] = deque(maxlen=cfg.phi_history)
    flags: deque[bool] = deque(maxlen=cfg.persist_n)
    cusum = 0.0
    open_: _Open | None = None
    out: list[AnomalyCandidate] = []
    warm_until = world_start + cfg.warmup_days * DAY
    for ts, num, den in windows:
        base = baseline(spec, cfg, index, ts)
        u = unit_z(spec, cfg, num, den, base) if base is not None else None
        if u is None:
            flags.append(False)
            continue
        z_unit, expected, excess = u
        phi = robust_phi(history, cfg)
        history.append(z_unit)
        a, direction = _oriented(spec, z_unit / math.sqrt(phi))
        excess_ok = abs(excess) >= cfg.min_excess_events
        flagged = excess_ok and a >= cfg.z_persist
        flags.append(flagged)
        fire_p = excess_ok and (a >= cfg.z_single or (flagged and sum(flags) >= cfg.persist_k))
        fire_c = False
        if spec.cusum:
            cusum = max(0.0, cusum + a - cfg.cusum_k)
            if cusum >= cfg.cusum_h and excess_ok:
                fire_c, cusum = True, 0.0
        if ts < warm_until:
            continue
        observed = num if spec.kind == "count" else num / den
        sample = num if spec.kind == "count" else den
        ev = evidence_id(spec.metric, spec.version, scope, spec.grain, ts)
        if open_ is None:
            if fire_p or fire_c:
                methods = (["z_persistence"] if fire_p else []) + (["cusum"] if fire_c else [])
                open_ = _Open(ts, ts + grain_s, ts + grain_s, direction, observed, expected, a, sample, methods, ev)
            continue
        if (flagged or fire_c) and direction == open_.direction:
            open_.end, open_.quiet = ts + grain_s, 0
            open_.score = max(open_.score, a)
            open_.evidence.append(ev)
            if fire_c:
                open_.methods.add("cusum")
            if fire_p:
                open_.methods.add("z_persistence")
        elif a < cfg.recovery_z:
            open_.quiet += 1
            if open_.quiet >= cfg.recovery_windows:
                out.append(_close(open_, "main", spec, scope, ts + grain_s))
                open_, cusum = None, 0.0
    if open_ is not None:
        out.append(_close(open_, "main", spec, scope, None))
    return out


def scan_static(spec: SeriesSpec, cfg: DetectorConfig, windows: Iterable[Window], world_start: int, grain_s: int,
                scope: tuple[tuple[str, str], ...]) -> list[AnomalyCandidate]:
    """Comparison system (spec 10): fixed band around the warm-up median, single-window firing."""
    windows = list(windows)
    warm_until = world_start + cfg.warmup_days * DAY
    ref_vals = [v for ts, n, d in windows if ts < warm_until
                and (spec.kind == "count" or d >= cfg.min_sample) and (v := _value(spec, n, d)) is not None]
    if not ref_vals:
        return []
    ref = median(ref_vals)
    open_: _Open | None = None
    out: list[AnomalyCandidate] = []
    for ts, num, den in windows:
        if ts < warm_until:
            continue
        if spec.kind == "rate" and den < cfg.min_sample:
            continue
        v = _value(spec, num, den)
        expected_events = ref * (den if spec.kind == "rate" else 1)
        events_off = abs((num if spec.kind == "rate" else v) - expected_events)
        if spec.kind == "count":
            hit_up, hit_down = v > ref * cfg.static_count_factor, v < ref / cfg.static_count_factor
        else:
            hit_up, hit_down = v > ref * cfg.static_rate_rise, v < ref * (1 - cfg.static_rate_drop)
        direction = "up" if hit_up else "down"
        hit = ((hit_up and spec.direction in ("up", "both")) or (hit_down and spec.direction in ("down", "both"))) \
            and events_off >= cfg.min_excess_events
        ev = evidence_id(spec.metric, spec.version, scope, spec.grain, ts)
        sample = num if spec.kind == "count" else den
        if open_ is None:
            if hit:
                score = abs(v - ref) / ref if ref else float(events_off)
                open_ = _Open(ts, ts + grain_s, ts + grain_s, direction, v, ref, score, sample, ["static"], ev)
            continue
        if hit and direction == open_.direction:
            open_.end, open_.quiet = ts + grain_s, 0
            open_.evidence.append(ev)
        else:
            open_.quiet += 1
            if open_.quiet >= cfg.recovery_windows:
                out.append(_close(open_, "static", spec, scope, ts + grain_s))
                open_ = None
    if open_ is not None:
        out.append(_close(open_, "static", spec, scope, None))
    return out
