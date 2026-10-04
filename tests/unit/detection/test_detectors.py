"""Detectors on hand-built series (no ClickHouse, no simulator): each behaviour the design promises."""

from __future__ import annotations

import math
import random
from dataclasses import replace

from anomalyos.detection.candidates import AnomalyCandidate, prefilter
from anomalyos.detection.config import DetectorConfig, SeriesSpec
from anomalyos.detection.detectors import DAY, robust_phi, scan_main, scan_static

H = 3600
T0 = 1_785_715_200
CFG = DetectorConfig()
RATE_H = SeriesSpec("T1", "authorization_rate", 1, "1h", (), "down", "rate")
RATE_D = SeriesSpec("T2", "authorization_rate", 1, "1d", (), "down", "rate", cusum=True)
UP_RATE = SeriesSpec("T3", "duplicate_charge_rate", 1, "1h", (), "up", "rate")
COUNT = SeriesSpec("T4", "attempt_volume", 1, "1h", (), "both", "count")


def binom(rng, n, p):
    return sum(rng.random() < p for _ in range(n))


def hourly_rate(days, n=200, p=lambda ts: 0.9, seed=1, extra_sd=0.0):
    rng = random.Random(seed)
    out = []
    for i in range(days * 24):
        ts = T0 + i * H
        pp = min(0.999, max(0.0, p(ts) * (1 + rng.gauss(0, extra_sd)))) if extra_sd else p(ts)
        out.append((ts, binom(rng, n, pp), n))
    return out


def hourly_counts(days, mu=lambda ts: 150.0, seed=2):
    rng = random.Random(seed)
    out = []
    for i in range(days * 24):
        ts = T0 + i * H
        lam = mu(ts)  # Poisson by inversion of exponentials (seeded, stdlib only)
        k, acc = 0, rng.expovariate(1.0)
        while acc < lam:
            k += 1
            acc += rng.expovariate(1.0)
        out.append((ts, k, 1))
    return out


def run(spec, windows, grain=H, system="main"):
    fn = scan_main if system == "main" else scan_static
    return fn(spec, CFG, windows, T0, grain, ())


def seasonal(ts):  # diurnal approval shape
    return 0.88 + 0.04 * math.sin(2 * math.pi * ((ts - T0) % DAY) / DAY)


def test_step_drop_is_detected_in_its_first_window_and_recovers():
    s, e = T0 + 6 * DAY + 10 * H, T0 + 6 * DAY + 14 * H
    windows = hourly_rate(10, p=lambda ts: 0.7 if s <= ts < e else 0.9)
    cands = run(RATE_H, windows)
    assert len(cands) == 1
    c = cands[0]
    assert c.direction == "down" and c.window_start == s and c.detected_at == s + H
    assert c.status == "recovered" and c.recovered_at <= e + 3 * H
    assert c.observed < c.expected and c.relative_delta < -0.15


def test_false_alarm_rate_on_stationary_seasonal_series_is_small():
    # 30 independent 3-week hourly series without any change; about 0.07 false alarms per series are expected
    alarms = sum(len(run(RATE_H, hourly_rate(21, p=seasonal, seed=s))) for s in range(30))
    assert alarms <= 4


def test_tail_calibration_of_the_stabilized_z():
    from anomalyos.detection.detectors import baseline, unit_z
    below = total = 0
    for seed in range(15):
        for pfun in (seasonal, lambda ts: 0.95, lambda ts: 0.6):
            windows = hourly_rate(21, p=pfun, seed=seed)
            index = {ts: (n, d) for ts, n, d in windows}
            for ts, n, d in windows:
                b = baseline(RATE_H, CFG, index, ts)
                if b:
                    total += 1
                    below += unit_z(RATE_H, CFG, n, d, b)[0] < -2.5
    assert 0.004 < below / total < 0.009  # nominal 0.0062; the plain normal z gave ~0.0126


def test_overdispersed_noise_is_absorbed_by_phi():
    no_phi = replace(CFG, phi_default=1.0, phi_min_history=10**9)  # binomial variance only
    with_phi = without_phi = 0
    for s in range(20):
        windows = hourly_rate(21, p=seasonal, seed=100 + s, extra_sd=0.03)
        with_phi += len(run(RATE_H, windows))
        without_phi += len(scan_main(RATE_H, no_phi, windows, T0, H, ()))
    assert with_phi <= 3 and without_phi >= 10 * max(with_phi, 1)  # measured: 1 vs 84


def test_robust_phi_estimates_the_variance_inflation():
    rng = random.Random(5)
    assert 2.5 < robust_phi([rng.gauss(0, math.sqrt(3.0)) for _ in range(500)], CFG) < 3.5
    assert robust_phi([0.1] * 50, CFG) == 1.0  # floored at 1
    assert robust_phi([1.0] * 3, CFG) == CFG.phi_default  # not enough history


def test_slow_drift_is_caught_by_cusum_on_the_daily_series():
    rng = random.Random(6)
    windows = []
    for d in range(28):
        p = 0.85 - (0.06 * min(1.0, (d - 14) / 7) if d >= 14 else 0.0)
        windows.append((T0 + d * DAY, binom(rng, 4000, p), 4000))
    cands = run(RATE_D, windows, grain=DAY)
    assert len(cands) == 1 and "cusum" in cands[0].method
    assert T0 + 14 * DAY < cands[0].detected_at <= T0 + 20 * DAY


def test_small_samples_and_zero_denominators_never_alarm():
    windows = [(ts, 0, 20) for ts, _, _ in hourly_rate(10)]  # approval 0 but only 20 attempts per window
    assert run(RATE_H, windows) == []
    windows = [(ts, n, d if i % 5 else 0) for i, (ts, n, d) in enumerate(hourly_rate(10))]
    windows = [(ts, 0 if d == 0 else n, d) for ts, n, d in windows]
    assert run(RATE_H, windows) == []


def test_a_single_rare_event_does_not_fire_but_a_burst_does():
    base = [(ts, 0, 400) for ts, _, _ in hourly_rate(10)]
    one = [(ts, 1 if ts == T0 + 6 * DAY else n, d) for ts, n, d in base]
    assert run(UP_RATE, one) == []  # 1 duplicate on 400 captures after zero baseline: below min excess events
    burst = [(ts, 40 if T0 + 6 * DAY <= ts < T0 + 6 * DAY + 3 * H else n, d) for ts, n, d in base]
    cands = run(UP_RATE, burst)
    assert len(cands) == 1 and cands[0].direction == "up" and cands[0].detected_at == T0 + 6 * DAY + H


def test_counts_detect_spikes_and_drops():
    spike = hourly_counts(10, mu=lambda ts: 450.0 if T0 + 6 * DAY <= ts < T0 + 6 * DAY + 2 * H else 150.0)
    up = run(COUNT, spike)
    assert len(up) == 1 and up[0].direction == "up"
    drop = hourly_counts(10, mu=lambda ts: 40.0 if T0 + 7 * DAY <= ts < T0 + 7 * DAY + 2 * H else 150.0, seed=7)
    down = run(COUNT, drop)
    assert len(down) == 1 and down[0].direction == "down"


def test_nothing_fires_during_warm_up():
    windows = hourly_rate(10, p=lambda ts: 0.5 if T0 + DAY <= ts < T0 + DAY + 5 * H else 0.9)
    assert run(RATE_H, windows) == []


def test_no_future_leakage_truncating_the_series_keeps_earlier_decisions():
    s1, s2 = T0 + 5 * DAY + 9 * H, T0 + 9 * DAY + 15 * H
    windows = hourly_rate(14, p=lambda ts: 0.72 if (s1 <= ts < s1 + 3 * H or s2 <= ts < s2 + 5 * H) else 0.9, seed=8)
    full = run(RATE_H, windows)
    assert len(full) == 2
    for cut in range(4 * 24, len(windows), 7):
        part = run(RATE_H, windows[:cut])
        cut_ts = windows[cut - 1][0] + H
        early_full = {(c.anomaly_id, c.detected_at) for c in full if c.detected_at <= cut_ts}
        assert {(c.anomaly_id, c.detected_at) for c in part} == early_full, cut


def test_ids_are_stable_and_runs_deterministic():
    windows = hourly_rate(10, p=lambda ts: 0.7 if T0 + 6 * DAY <= ts < T0 + 6 * DAY + 3 * H else 0.9)
    a, b = run(RATE_H, windows), run(RATE_H, list(windows))
    assert a == b and a[0].anomaly_id.startswith("anom_") and all(e.startswith("evd_") for e in a[0].evidence_ids)


def test_static_system_detects_a_large_step_but_ignores_nothing_clever():
    s = T0 + 6 * DAY + 10 * H
    windows = hourly_rate(10, p=lambda ts: 0.6 if s <= ts < s + 3 * H else 0.9)
    cands = run(RATE_H, windows, system="static")
    assert cands and cands[0].method == ("static",) and cands[0].detected_at == s + H


def _cand(**kw) -> AnomalyCandidate:
    base = dict(anomaly_id="anom_x", system="main", series_key="S1", metric="authorization_rate", metric_version=1,
                grain="1h", scope=(), direction="down", window_start=0, window_end=3600, detected_at=3600, observed=0.7,
                expected=0.9, delta=-0.2, relative_delta=-0.22, score=5.0, sample_size=200, method=("z_persistence",),
                evidence_ids=("evd_1",))
    base.update(kw)
    return AnomalyCandidate(**base)


def test_prefilter_suppresses_low_volume_and_links_sub_scopes_to_the_global_candidate():
    g = _cand()
    child = _cand(anomaly_id="anom_c", series_key="S2", scope=(("psp", "psp_beta"),), window_start=1800, window_end=7200)
    tiny = _cand(anomaly_id="anom_t", series_key="S2", scope=(("psp", "psp_alpha"),), sample_size=10, window_start=90000,
                 window_end=93600, detected_at=93600)
    out = {c.anomaly_id: c for c in prefilter([tiny, child, g], CFG.min_sample)}
    assert out["anom_c"].parent_id == "anom_x" and out["anom_c"].status == "promoted"
    assert out["anom_t"].status == "suppressed" and out["anom_t"].status_reason == "insufficient_data"


def test_ratio_metrics_above_one_do_not_crash():
    spec = SeriesSpec("T5", "subscription_cancellation_rate", 1, "1d", (), "up", "rate", cusum=True)
    windows = [(T0 + d * DAY, 60 if d == 10 else 35, 40) for d in range(20)]  # 1.5 cancellations per attempt on day 10
    cands = scan_main(spec, CFG, windows, T0, DAY, ())
    assert all(c.direction == "up" for c in cands)


def test_as_of_view_equals_what_was_known_at_detection():
    # ADR-039 D-0: the first-look view must match a run whose data end at detected_at
    from anomalyos.detection.candidates import as_of_view, concurrent_at
    s = T0 + 6 * DAY + 10 * H
    windows = hourly_rate(10, p=lambda ts: 0.78 if s <= ts < s + H else 0.5 if s + H <= ts < s + 6 * H else 0.9, seed=3)
    full = run(RATE_H, windows)
    assert len(full) == 1 and full[0].score > full[0].score_at_detection and full[0].status == "recovered"
    v = as_of_view(full[0])
    cut = [w for w in windows if w[0] < full[0].detected_at]
    part = run(RATE_H, cut)[0]
    keys = ("anomaly_id", "window_start", "window_end", "detected_at", "observed", "expected", "score", "method",
            "evidence_ids", "status", "recovered_at", "score_at_detection", "methods_at_detection")
    assert {k: getattr(v, k) for k in keys} == {k: getattr(part, k) for k in keys}
    # concurrency uses only candidates already detected and not yet recovered before the window
    later = replace(full[0], anomaly_id="anom_later", detected_at=full[0].detected_at + H)
    ended = replace(full[0], anomaly_id="anom_ended", recovered_at=full[0].window_start)
    assert concurrent_at(full[0], [full[0], later, ended]) == []
