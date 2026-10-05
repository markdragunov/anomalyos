"""Input validation, injection safety and determinism of the metrics layer."""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from pulseos.metrics import ALLOWED_DIMENSIONS, GRAINS, MetricError, compute, get_metric, list_metrics
from pulseos.metrics.compute import MAX_WINDOWS
from tests.unit.metrics.conftest import DB, RUN, T0, load_rows, norm_row

START = datetime.fromtimestamp(T0, tz=timezone.utc)
END = START + timedelta(hours=2)


class NoRunner:
    """Validation must fail before any SQL is sent."""

    def rows(self, sql, params):  # pragma: no cover - must never be called
        raise AssertionError("SQL was sent for an invalid request")


def call(**kw):
    args = dict(runner=NoRunner(), database=DB, run_id=RUN, metric="authorization_rate", version=1, start=START, end=END, grain="1h")
    args.update(kw)
    return compute(**args)


@pytest.mark.parametrize("kw, msg", [
    (dict(metric="nope"), "unknown metric"),
    (dict(version=2), "unknown metric"),
    (dict(grain="2h"), "unknown grain"),
    (dict(grain="1h; DROP TABLE x"), "unknown grain"),
    (dict(start=START + timedelta(minutes=5)), "aligned"),
    (dict(start=datetime(2026, 8, 3)), "timezone-aware"),
    (dict(end=START), "after start"),
    (dict(end=START + timedelta(days=400), grain="5m"), "windows exceed"),
    (dict(database="anomalyos; DROP TABLE x"), "invalid database"),
    (dict(run_id="run_x' OR 1=1 --"), "invalid run_id"),
    (dict(group_by=("psp; DROP",)), "not a cohort dimension"),
    (dict(group_by=("scenario_id",)), "not a cohort dimension"),
    (dict(filters={"event_type": "x"}), "not a cohort dimension"),
    (dict(filters={"psp": 5}), "expects strings"),
    (dict(filters={"attempt_no": "1"}), "expects integers"),
    (dict(filters={"attempt_no": 256}), "expects integers"),
    (dict(filters={"psp": []}), "needs 1.."),
    (dict(metric="checkout_conversion_rate", group_by=("payment_method_type",)), "not available"),
    (dict(metric="renewal_success_rate", filters={"card_brand": "visa"}), "not available"),
    (dict(metric="duplicate_charge_rate", filters={"channel": "checkout"}), "not available"),
])
def test_invalid_requests_raise_before_any_sql(kw, msg):
    with pytest.raises(MetricError, match=msg):
        call(**kw)


def test_unknown_metric_error_lists_known_metrics():
    with pytest.raises(KeyError, match="authorization_rate"):
        get_metric("nope", 1)


def test_registry_is_consistent():
    metrics = list_metrics()
    assert len({m.key for m in metrics}) == len(metrics)
    for m in metrics:
        assert set(m.allowed_dims) <= set(ALLOWED_DIMENSIONS) and set(m.required_group_by) <= set(m.allowed_dims)
        assert m.version >= 1 and m.numerator and m.denominator
    assert set(GRAINS) == {"5m", "15m", "1h", "1d"}
    assert get_metric("revenue_collected_minor", 1).required_group_by == ("currency",)


def test_window_limit_is_bounded():
    assert MAX_WINDOWS >= 28 * 24 * 12  # 28 days of 5-minute windows must fit


@pytest.fixture(scope="module")
def tiny(ch):
    load_rows(ch, [norm_row("payment.authorized", T0 + 60, attempt_no=1), norm_row("payment.declined", T0 + 120, attempt_no=1),
                   norm_row("payment.authorized", T0 + 180, attempt_no=1, psp="psp_beta")])


def test_sql_injection_in_filter_values_is_bound_not_interpolated(ch, tiny):
    evil = ["x' OR 1=1 --", "'); DROP TABLE anomalyos_metrics_test.events_norm; --", "psp_alpha' OR psp != '", "\\", "%(psp)s", "{start:UInt32}"]
    for value in evil:
        pts = compute(ch.runner, ch.db, RUN, "authorization_rate", 1, START, END, "1h", {"psp": value})
        assert [(p.numerator, p.denominator) for p in pts] == [(0, 0), (0, 0)]  # matches nothing, no error
    assert int(ch.client.command(f"SELECT count() FROM {ch.db}.events_norm")) == 3  # table intact
    ok = compute(ch.runner, ch.db, RUN, "authorization_rate", 1, START, END, "1h", {"psp": "psp_beta"})
    assert (ok[0].numerator, ok[0].denominator) == (1, 1)


def test_results_are_deterministic_and_compact(ch, tiny):
    a = compute(ch.runner, ch.db, RUN, "authorization_rate", 1, START, END, "1h", {}, ("psp",))
    b = compute(ch.runner, ch.db, RUN, "authorization_rate", 1, START, END, "1h", {}, ("psp",))
    assert a == b
    assert len(a) == 2 * 2  # 2 groups x 2 windows: the result size is windows x groups, never raw rows


def test_other_runs_are_invisible(ch, tiny):
    other = "run_00000000000000b2"
    pts = compute(ch.runner, ch.db, other, "authorization_rate", 1, START, END, "1h")
    assert all(p.denominator == 0 and p.value is None for p in pts)


def test_metrics_and_events_code_never_touch_ground_truth():
    root = pathlib.Path(__file__).resolve().parents[3] / "src" / "pulseos"
    hits = [str(p.relative_to(root)) for pkg in ("metrics", "events") for p in (root / pkg).rglob("*.py")
            if any(m in p.read_text(encoding="utf-8") for m in ("_truth", "ground_truth", "scenario_id"))]
    assert hits == [], hits
