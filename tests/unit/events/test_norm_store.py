"""events_norm DDL, idempotent load and SQL checks on embedded ClickHouse (chdb, optional)."""

from __future__ import annotations

import pytest

from pulseos.events import store
from pulseos.simulation import clickhouse_load as chl


def test_ddl_is_validated_and_partitioned_by_run():
    stmts = store.ddl("anomalyos")
    assert "PARTITION BY run_id" in stmts[1] and "events_norm" in stmts[1]
    with pytest.raises(ValueError):
        store.ddl("anomalyos; DROP TABLE x")
    with pytest.raises(ValueError):
        store.norm_sql_checks("anomalyos", "run_x' OR 1=1 --")


def test_load_checks_idempotent_reload_and_negative_checks(full_run, tmp_path):
    pytest.importorskip("chdb", reason="optional: embedded ClickHouse for SQL tests (pip install chdb)")
    from tests.unit.simulation.sim_chdb_client import ChdbClient

    c = ChdbClient(str(tmp_path / "ch"))
    try:
        with pytest.raises(RuntimeError, match="not loaded"):
            store.load_norm(c, full_run["dir"], "anomalyos")  # raw layer missing -> refuse
        chl.load_run(c, full_run["dir"], "anomalyos")
        out = store.load_norm(c, full_run["dir"], "anomalyos")
        assert out["norm_rows"] > out["raw_events"] * 0.5 and out["checks"] and all(v == 0 for v in out["checks"].values()), out["checks"]
        with pytest.raises(RuntimeError, match="already normalized"):
            store.load_norm(c, full_run["dir"], "anomalyos")
        again = store.load_norm(c, full_run["dir"], "anomalyos", replace=True)
        assert again["norm_rows"] == out["norm_rows"]  # reload is idempotent
        run_id = out["run_id"]
        # The checks must fire on bad data.
        c.command(f"INSERT INTO anomalyos.events_norm (run_id, event_id, event_type, schema_version, occurred_at, ingested_at, "
                  f"merchant_id, entity_id, customer_id, payment_intent_id, customer_country, issuer_country, currency, psp, "
                  f"payment_method_type, card_brand, platform, app_version, plan_id, channel, attempt_no, amount_minor, status, "
                  f"idempotency_key_present, raw_event_id, raw_seq) VALUES ('{run_id}', 'dup', 'payment.authorized', 'norm-1', "
                  f"1786000100, 1786000000, 'm', 'e', 'c', 'p', '', 'FR', 'eur', 'psp_alpha', 'card', 'visa', 'web', 'web', "
                  f"'unknown', 'checkout', 0, 1, 'authorized', 1, 'evt_missing', 0)")
        bad = store.run_norm_checks(c.command, "anomalyos", run_id)
        assert bad["norm_row_without_raw_event"] == 1
        assert bad["empty_string_dimensions"] == 1
        assert bad["ingested_before_occurred"] == 1
        assert bad["attempt_event_without_attempt_no"] == 1
        assert bad["authorized_not_equal_succeeded_charges"] == 1
    finally:
        c.close()
