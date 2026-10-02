"""Every metric equals a straightforward Python reference on a small hand-built normalized stream."""

from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone

import pytest

from anomalyos.metrics import compute, get_metric, list_metrics
from tests.unit.metrics.conftest import DB, RUN, T0, load_rows, norm_row

HOUR = 3600
START = datetime.fromtimestamp(T0, tz=timezone.utc)
END = START + timedelta(hours=3, minutes=30)  # trailing window 03:00 is incomplete
END_TS = T0 + 3 * HOUR + 1800


def build_rows() -> list[dict]:
    rows: list[dict] = []
    # --- checkouts in hour 0 and 2, authorizations/declines/failures, retries, fraud flags
    for i in range(16):
        ts = T0 + 60 * i + (2 * HOUR if i >= 10 else 0)
        psp = "psp_alpha" if i % 2 == 0 else "psp_beta"
        plat = "web" if i % 3 else "android"
        pi = f"pi_{i}"
        base = dict(payment_intent_id=pi, psp=psp, platform=plat, customer_id=f"cus_{i}", amount_minor=1000 + i, entity_id=pi)
        rows.append(norm_row("checkout.started", ts, **base))
        pattern = i % 4
        ch = dict(base, entity_id=f"ch_{i}_1")
        if pattern == 0:  # clean first-attempt success
            rows += [norm_row("payment.authorized", ts + 5, attempt_no=1, **ch), norm_row("payment.captured", ts + 5, attempt_no=1, **ch)]
        elif pattern == 1:  # declined, then retry succeeds
            rows.append(norm_row("payment.declined", ts + 5, attempt_no=1, decline_code="do_not_honor", error_code="card_declined", **ch))
            ch2 = dict(base, entity_id=f"ch_{i}_2")
            rows += [norm_row("payment.authorized", ts + 20, attempt_no=2, **ch2), norm_row("payment.captured", ts + 20, attempt_no=2, **ch2)]
        elif pattern == 2:  # technical failure, never converts
            rows.append(norm_row("payment.failed", ts + 5, attempt_no=1, error_code="processing_error", **ch))
        else:  # fraud-blocked decline, never converts
            rows += [norm_row("fraud.flagged", ts + 5, attempt_no=1, decline_code="highest_risk_level", **ch),
                     norm_row("payment.declined", ts + 5, attempt_no=1, decline_code="highest_risk_level", error_code="card_declined", **ch)]
    # a checkout that authorizes only AFTER `end` must not count as converted (no future leakage)
    late = dict(payment_intent_id="pi_late", customer_id="cus_late", entity_id="pi_late")
    rows.append(norm_row("checkout.started", T0 + 3 * HOUR + 600, **late))
    rows.append(norm_row("payment.authorized", END_TS + 60, attempt_no=1, **dict(late, entity_id="ch_late")))
    # --- renewals: hour 0 (3 attempts, 2 renewed), hour 2 (1 attempt, 0 renewed + 1 renewed from a prior attempt)
    for k in range(3):
        rows.append(norm_row("subscription.renewal_attempted", T0 + 100 + k, channel="renewal", plan_id="monthly_eur", customer_id=f"cus_r{k}"))
    rows += [norm_row("subscription.renewed", T0 + 400 + k, channel="renewal", plan_id="monthly_eur") for k in range(2)]
    rows.append(norm_row("subscription.renewal_attempted", T0 + 2 * HOUR + 100, channel="renewal", plan_id="monthly_usd", currency="usd"))
    rows.append(norm_row("subscription.renewed", T0 + 2 * HOUR + 200, channel="renewal", plan_id="monthly_eur"))
    # --- refunds: hour 0 alongside captures; hour 2 refunds exist but (see below) captures exist too -> use hour 1 for a pure-refund window
    rows += [norm_row("refund.requested", T0 + 700 + k, channel="checkout") for k in range(2)]
    rows += [norm_row("refund.succeeded", T0 + 700 + k, channel="checkout") for k in range(2)]
    rows += [norm_row("refund.succeeded", T0 + HOUR + 30, channel="checkout")]  # hour 1: refund, zero captures -> refund_rate has denominator 0
    # --- duplicates (checkout captures, no idempotency key): second within 60 s of first is a duplicate
    dup = dict(customer_id="cus_dup", amount_minor=4242, payment_intent_id="pi_d")
    rows += [norm_row("payment.captured", T0 + 900, idempotency_key_present=0, attempt_no=1, **dup),
             norm_row("payment.captured", T0 + 930, idempotency_key_present=0, attempt_no=1, **dup)]      # duplicate
    rows += [norm_row("payment.captured", T0 + 1000, idempotency_key_present=0, attempt_no=1, customer_id="cus_s", amount_minor=77),
             norm_row("payment.captured", T0 + 1090, idempotency_key_present=0, attempt_no=1, customer_id="cus_s", amount_minor=77)]  # 90 s: not
    rows += [norm_row("payment.captured", T0 + 1100, idempotency_key_present=0, attempt_no=1, customer_id="cus_k", amount_minor=88),
             norm_row("payment.captured", T0 + 1110, idempotency_key_present=1, attempt_no=1, customer_id="cus_k", amount_minor=88)]  # keyed: not
    # exactly on the 60 s boundary: 60 s is a duplicate, 61 s is not
    rows += [norm_row("payment.captured", T0 + 1300, idempotency_key_present=0, attempt_no=1, customer_id="cus_60", amount_minor=60),
             norm_row("payment.captured", T0 + 1360, idempotency_key_present=0, attempt_no=1, customer_id="cus_60", amount_minor=60),
             norm_row("payment.captured", T0 + 1400, idempotency_key_present=0, attempt_no=1, customer_id="cus_61", amount_minor=61),
             norm_row("payment.captured", T0 + 1461, idempotency_key_present=0, attempt_no=1, customer_id="cus_61", amount_minor=61)]
    # pair straddling the 03:00 boundary: the first capture lies before the window start used in the lookback test
    rows += [norm_row("payment.captured", T0 + 3 * HOUR - 30, idempotency_key_present=0, attempt_no=1, customer_id="cus_b", amount_minor=99),
             norm_row("payment.captured", T0 + 3 * HOUR + 10, idempotency_key_present=0, attempt_no=1, customer_id="cus_b", amount_minor=99)]
    # --- money in two currencies
    rows += [norm_row("payment.captured", T0 + 1200 + k, attempt_no=1, currency="usd", amount_minor=500 + k) for k in range(3)]
    # --- events at/after `end` for every metric type must be invisible
    rows += [norm_row("payment.authorized", END_TS + 1, attempt_no=1), norm_row("payment.captured", END_TS + 1, attempt_no=1),
             norm_row("subscription.renewed", END_TS + 1, channel="renewal"), norm_row("refund.succeeded", END_TS + 1)]
    return rows


@pytest.fixture(scope="module")
def stream(ch):
    rows = build_rows()
    load_rows(ch, rows)
    return rows


# --------------------------------------------------------------------------- python references
ATTEMPTS = ("payment.authorized", "payment.declined", "payment.failed")


def _in_range(rows, start_ts=T0, end_ts=END_TS):
    return [r for r in rows if start_ts <= r["occurred_at"] < end_ts]


def _count(rows, *types, **eq):
    return sum(1 for r in rows if r["event_type"] in types and all(r[k] == v for k, v in eq.items()))


def reference(metric: str, rows: list[dict], window_rows: list[dict], start_ts=T0, end_ts=END_TS) -> tuple[int, int]:
    """(numerator, denominator) for one window/group, written independently of the SQL."""
    w = window_rows
    if metric == "authorization_rate":
        return _count(w, "payment.authorized"), _count(w, *ATTEMPTS)
    if metric == "first_attempt_authorization_rate":
        f = [r for r in w if r["attempt_no"] == 1]
        return _count(f, "payment.authorized"), _count(f, *ATTEMPTS)
    if metric == "technical_failure_rate":
        return _count(w, "payment.failed"), _count(w, *ATTEMPTS)
    if metric == "checkout_conversion_rate":
        started = [r for r in w if r["event_type"] == "checkout.started"]
        authorized = {r["payment_intent_id"] for r in _in_range(rows, start_ts, end_ts)
                      if r["event_type"] == "payment.authorized" and r["channel"] == "checkout"}
        return sum(1 for r in started if r["payment_intent_id"] in authorized), len(started)
    if metric == "renewal_success_rate":
        return _count(w, "subscription.renewed"), _count(w, "subscription.renewal_attempted")
    if metric == "refund_rate":
        return _count(w, "refund.succeeded"), _count(w, "payment.captured")
    if metric == "fraud_flag_rate":
        return _count(w, "fraud.flagged"), _count(w, *ATTEMPTS)
    if metric == "attempt_volume":
        return _count(w, *ATTEMPTS), 1
    if metric == "revenue_collected_minor":
        return sum(r["amount_minor"] for r in w if r["event_type"] == "payment.captured"), 1
    raise AssertionError(metric)


def duplicate_reference(rows, window_rows, start_ts=T0, end_ts=END_TS):
    caps = sorted((r for r in rows if r["event_type"] == "payment.captured" and r["channel"] == "checkout" and r["occurred_at"] < end_ts),
                  key=lambda r: (r["occurred_at"], r["raw_seq"]))
    last: dict[tuple, int] = {}
    dup_ids = set()
    for r in caps:
        key = (r["customer_id"], r["amount_minor"], r["currency"])
        if key in last and not r["idempotency_key_present"] and r["occurred_at"] - last[key] <= 60:
            dup_ids.add(r["raw_seq"])
        last[key] = r["occurred_at"]
    w = [r for r in window_rows if r["event_type"] == "payment.captured" and r["channel"] == "checkout"]
    return sum(1 for r in w if r["raw_seq"] in dup_ids), len(w)


def reference_series(metric, rows, grain_s, group_dims=(), start_ts=T0, end_ts=END_TS, filters=None):
    rows = [r for r in rows if all(r[k] == v for k, v in (filters or {}).items())]
    n = -(-(end_ts - start_ts) // grain_s)
    in_range = _in_range(rows, start_ts, end_ts)
    by = collections.defaultdict(list)
    for r in in_range:
        by[(tuple(r[d] for d in group_dims), (r["occurred_at"] - start_ts) // grain_s)].append(r)
    d = get_metric(metric, 1)
    groups = sorted({g for g, _ in by})
    out = []
    for g in groups:
        for i in range(n):
            w = by.get((g, i), [])
            if metric == "duplicate_charge_rate":
                num, den = duplicate_reference(rows, w, start_ts, end_ts)
            else:
                num, den = reference(metric, rows, w, start_ts, end_ts) if w else (0, d.empty_denominator)
            out.append((g, start_ts + i * grain_s, num, den))
    return out


def assert_series(got, want, metric):
    """Point-wise equality by (group, window); groups the SQL never saw must be all-empty in the reference."""
    empty = (0, get_metric(metric, 1).empty_denominator)
    got_map = {(g, ts): (n, d) for g, ts, n, d in got}
    want_map = {(g, ts): (n, d) for g, ts, n, d in want}
    seen_groups = {g for g, _ in got_map}
    assert got_map == {k: v for k, v in want_map.items() if k[0] in seen_groups}
    assert all(v == empty for k, v in want_map.items() if k[0] not in seen_groups)
    assert [(ts, g) for g, ts, _, _ in got] == sorted((ts, g) for g, ts, _, _ in got)  # ordered by window, then group


def run_metric(ch, metric, grain="1h", group_by=(), filters=None, start=START, end=END):
    pts = compute(ch.runner, ch.db, RUN, metric, 1, start, end, grain, filters, group_by)
    return [(tuple(v for _, v in p.dims), int(p.window_start.timestamp()), p.numerator, p.denominator) for p in pts], pts


METRICS = [m.name for m in list_metrics()]


@pytest.mark.parametrize("metric", METRICS)
def test_metric_equals_python_reference_ungrouped(ch, stream, metric):
    group = ("currency",) if metric == "revenue_collected_minor" else ()
    got, pts = run_metric(ch, metric, group_by=group)
    want = reference_series(metric, stream, HOUR, group_dims=group)
    assert_series(got, want, metric)
    assert any(n or d for _, _, n, d in got)  # the fixture exercises every metric


@pytest.mark.parametrize("metric", [m for m in METRICS if m not in ("checkout_conversion_rate", "renewal_success_rate", "refund_rate")])
def test_metric_equals_python_reference_grouped_by_psp(ch, stream, metric):
    group = ("currency", "psp") if metric == "revenue_collected_minor" else ("psp",)
    got, _ = run_metric(ch, metric, group_by=group)
    assert_series(got, reference_series(metric, stream, HOUR, group_dims=group), metric)


def test_conversion_grouped_by_platform_and_15_minute_grain(ch, stream):
    got, _ = run_metric(ch, "checkout_conversion_rate", grain="15m", group_by=("platform",))
    assert_series(got, reference_series("checkout_conversion_rate", stream, 900, group_dims=("platform",)), "checkout_conversion_rate")


def test_filters_match_python_reference(ch, stream):
    got, _ = run_metric(ch, "authorization_rate", filters={"psp": "psp_beta", "platform": ["web", "android"]})
    assert_series(got, reference_series("authorization_rate", stream, HOUR, filters={"psp": "psp_beta"}), "authorization_rate")


# --------------------------------------------------------------------------- series contract
def test_zero_denominator_window_has_null_value_never_zero_or_nan(ch, stream):
    _, pts = run_metric(ch, "refund_rate")
    hour1 = next(p for p in pts if p.window_start == START + timedelta(hours=1))
    assert (hour1.numerator, hour1.denominator, hour1.value) == (1, 0, None)  # refund in a window with no captures
    empty = run_metric(ch, "authorization_rate")[1]
    h1 = next(p for p in empty if p.window_start == START + timedelta(hours=1))
    assert h1.denominator == 0 and h1.value is None
    assert all(p.value is None or 0.0 <= p.value <= 1.0 for p in pts + empty)


def test_trailing_window_is_incomplete_and_future_events_are_excluded(ch, stream):
    _, pts = run_metric(ch, "authorization_rate")
    assert [p.is_complete for p in pts] == [True, True, True, False]
    assert pts[-1].window_start == START + timedelta(hours=3)
    # events at/after `end` are invisible to every metric (checked by equality with the reference, which also drops them)
    total = sum(p.numerator for p in run_metric(ch, "attempt_volume")[1])
    assert total == _count(_in_range(stream), *ATTEMPTS)


def test_conversion_ignores_authorizations_at_or_after_end(ch, stream):
    _, pts = run_metric(ch, "checkout_conversion_rate")
    last = next(p for p in pts if p.window_start == START + timedelta(hours=3))
    assert (last.numerator, last.denominator) == (0, 1)  # pi_late authorizes only after `end`


def test_duplicate_window_lookback_sees_captures_before_start(ch, stream):
    start = START + timedelta(hours=3)
    pts = compute(ch.runner, ch.db, RUN, "duplicate_charge_rate", 1, start, END, "1h")
    # cus_b's earlier capture (03:00 - 30 s) is before `start` but must still mark the 03:00 + 10 s capture as a duplicate,
    # while the window itself counts only captures inside [start, end)
    assert [(p.numerator, p.denominator) for p in pts] == [(1, 1)]
    assert [(n, d) for _, _, n, d in reference_series("duplicate_charge_rate", stream, HOUR, start_ts=T0 + 3 * HOUR)] == [(1, 1)]


def test_revenue_is_always_per_currency(ch, stream):
    _, pts = run_metric(ch, "revenue_collected_minor", group_by=())
    assert {dict(p.dims)["currency"] for p in pts} == {"eur", "usd"}
    assert all(p.denominator == 1 for p in pts)


def test_every_registered_metric_is_covered_by_a_reference():
    assert set(METRICS) == {
        "authorization_rate", "first_attempt_authorization_rate", "technical_failure_rate", "checkout_conversion_rate",
        "renewal_success_rate", "refund_rate", "duplicate_charge_rate", "fraud_flag_rate", "attempt_volume", "revenue_collected_minor"}
