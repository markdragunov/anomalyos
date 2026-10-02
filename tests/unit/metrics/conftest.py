"""ClickHouse for metric tests: a live server when integration is on (CI), else embedded chdb, else skip."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from anomalyos.events import store
from anomalyos.events.normalize import NORM_COLUMN_NAMES, MERCHANT_ID
from anomalyos.metrics.compute import ClickHouseRunner
from tests.unit.simulation.conftest import full_run  # noqa: F401  (seeded session fixture reused from Stage 1)

DB = "anomalyos_metrics_test"
RUN = "run_00000000000000a1"
T0 = 1_785_715_200  # 2026-08-03 00:00:00 UTC (a grain-aligned Monday)


def _element(value: Any) -> str:
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return str(value)


def _literal(value: Any) -> str:
    """chdb takes parameters as text: scalars raw, arrays as ClickHouse literals (clickhouse_connect formats them itself)."""
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_element(v) for v in value) + "]"
    return str(value)


class ChdbRunner:
    def __init__(self, client):
        self._client = client

    def rows(self, sql: str, params: Mapping[str, Any]) -> list[tuple]:
        out = self._client.s.query(sql, "JSONCompact", params={k: _literal(v) for k, v in params.items()})
        return [tuple(r) for r in json.loads(out.bytes().decode())["data"]]


@dataclass
class Env:
    client: Any
    runner: Any
    db: str
    kind: str


@pytest.fixture(scope="module")
def ch(tmp_path_factory):
    if os.environ.get("ANOMALYOS_RUN_INTEGRATION") == "1":
        from anomalyos.simulation.clickhouse_load import connect, target_from_settings

        client = connect(target_from_settings())
        client.command(f"DROP DATABASE IF EXISTS {DB}")
        for stmt in store.ddl(DB):
            client.command(stmt)
        yield Env(client, ClickHouseRunner(client), DB, "live")
        client.command(f"DROP DATABASE IF EXISTS {DB}")
        return
    pytest.importorskip("chdb", reason="optional: embedded ClickHouse for metric SQL tests (pip install chdb)")
    from tests.unit.simulation.sim_chdb_client import ChdbClient

    client = ChdbClient(str(tmp_path_factory.mktemp("chdb")))
    for stmt in store.ddl(DB):
        client.command(stmt)
    yield Env(client, ChdbRunner(client), DB, "chdb")
    client.close()


def norm_row(event_type: str, ts: int, **kw: Any) -> dict[str, Any]:
    """A normalized row with explicit-unknown defaults (same shape the normalizer emits)."""
    row: dict[str, Any] = {
        "run_id": RUN, "event_id": f"e{ts}-{event_type}-{kw.get('entity_id', '')}-{kw.get('seq', 0)}", "event_type": event_type,
        "schema_version": "norm-1", "occurred_at": ts, "ingested_at": ts, "merchant_id": MERCHANT_ID, "entity_id": "x",
        "customer_id": "cus_0", "payment_intent_id": "pi_0", "customer_country": "DE", "issuer_country": "DE", "currency": "eur",
        "psp": "psp_alpha", "payment_method_type": "card", "card_brand": "visa", "platform": "web", "app_version": "web",
        "plan_id": "unknown", "channel": "checkout", "attempt_no": 0, "amount_minor": 1000, "status": "x",
        "decline_code": None, "error_code": None, "idempotency_key_present": 1, "raw_event_id": "evt_x", "raw_seq": 0,
    }
    kw.pop("seq", None)
    row.update(kw)
    return row


def load_rows(env: Env, rows: list[dict[str, Any]]) -> None:
    env.client.command(f"ALTER TABLE {env.db}.events_norm DROP PARTITION '{RUN}'")
    for i, r in enumerate(rows):
        r["raw_seq"] = i
        r["event_id"] = f"{r['event_id']}#{i}"
    block = ("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n").encode()
    env.client.raw_insert(f"{env.db}.events_norm", NORM_COLUMN_NAMES, block, fmt="JSONEachRow")
