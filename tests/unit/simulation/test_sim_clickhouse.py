"""ClickHouse schema/loader. Pure parts always run; SQL runs on embedded ClickHouse (chdb) if present."""

from __future__ import annotations

import json

import pytest

from pulseos.simulation import clickhouse_load as chl


def test_flatten_is_pure_and_typed(full_run):
    lines, evs = full_run["lines"], full_run["events"]
    i = next(k for k, e in enumerate(evs) if e["type"] == "charge.failed")
    row = chl.flatten(evs[i], "run_0123456789abcdef", i, lines[i])
    assert set(row) == set(chl.EVENT_COLUMN_NAMES)
    assert row["charge_id"].startswith("ch_") and row["payment_intent_id"].startswith("pi_")
    assert row["psp"] and row["customer_country"] and row["issuer_country"] and row["failure_code"] and row["amount"] > 0
    assert json.loads(row["data"]) == evs[i]
    assert chl.flatten(evs[i], "run_0123456789abcdef", i, lines[i]) == row
    j = next(k for k, e in enumerate(evs) if e["type"] == "customer.created")
    cust = chl.flatten(evs[j], "run_0123456789abcdef", j, lines[j])
    assert cust["psp"] == cust["card_brand"] == cust["issuer_country"] == chl.UNKNOWN  # explicit, never ''


def test_view_does_not_expose_low_cardinality_booleans():
    # Regression: `status = 'succeeded' AS ok` over a LowCardinality column is LowCardinality(UInt8),
    # which a real ClickHouse 25.8 refuses to CREATE (code 455). Found by the first live CI run.
    view = next(s for s in chl.ddl("anomalyos", "anomalyos_truth") if "v_charge_attempts" in s)
    assert "CAST(status = 'succeeded' AS UInt8) AS ok" in view


def test_identifiers_are_validated():
    with pytest.raises(ValueError):
        chl.ddl("anomalyos; DROP TABLE x", "t")
    with pytest.raises(ValueError):
        chl.sql_checks("anomalyos", "run_x' OR 1=1 --")


def test_load_and_sql_checks_on_embedded_clickhouse(full_run, tmp_path):
    pytest.importorskip("chdb", reason="optional: embedded ClickHouse for SQL tests (pip install chdb)")
    from .sim_chdb_client import ChdbClient

    c = ChdbClient(str(tmp_path / "ch"))
    try:
        out = chl.load_run(c, full_run["dir"], "anomalyos")
        assert out["events"] == full_run["res"].events
        assert out["checks"] and all(v == 0 for v in out["checks"].values()), out["checks"]
        assert str(c.command("SELECT toTypeName(ok) FROM anomalyos.v_charge_attempts LIMIT 1")).strip('"') == "UInt8"
        with pytest.raises(RuntimeError):
            chl.load_run(c, full_run["dir"], "anomalyos")
        assert chl.load_run(c, full_run["dir"], "anomalyos", replace=True)["events"] == out["events"]
        # Checks must fire on bad data: add an orphan charge row.
        c.command(f"INSERT INTO anomalyos.events (run_id, id, type, created, object_type, object_id, payment_intent_id, data) "
                  f"VALUES ('{out['run_id']}', 'evt_x', 'charge.failed', 1786000000, 'charge', 'ch_x', 'pi_missing', "
                  f"'card 4242424242424242')")
        bad = chl.run_sql_checks(c.command, "anomalyos", out["run_id"])
        assert bad["charge→payment_intent"] == 1 and bad["pan_like_digit_runs"] == 1
        # Ground truth lives in its own database, never in events.
        assert c.command("SELECT count() FROM anomalyos_truth.ground_truth") == len(full_run["res"].ground_truth)
    finally:
        c.close()


def test_target_comes_from_load_settings():
    t = chl.target_from_settings({"CLICKHOUSE_HOST": "db.local", "CLICKHOUSE_HTTP_PORT": "18123",
                                  "CLICKHOUSE_DB": "anom", "CLICKHOUSE_USER": "u", "CLICKHOUSE_PASSWORD": "s3cret"})
    assert (t.host, t.port, t.database, t.username, t.truth_database) == ("db.local", 18123, "anom", "u", "anom_truth")
    assert "s3cret" not in repr(t)
