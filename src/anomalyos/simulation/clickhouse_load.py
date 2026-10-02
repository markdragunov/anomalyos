"""ClickHouse schema and loader for generated runs.

Why: ClickHouse is the analytical source of truth (AGENTS.md rule 1). Events are loaded with
their full immutable envelope *and* flattened, typed dimension columns so later stages aggregate
in SQL (approval by psp × country × hour, …) instead of pulling rows into Python.
Ground truth goes to a **separate database** (``<db>_truth``) so analytics/agent credentials can
be scoped to the events database only.

Input:  a run directory written by ``runner.generate`` and a ``clickhouse_connect`` client.
Output: rows in ``<db>.events``, ``<db>_truth.ground_truth``, ``<db>_truth.runs``; a view
        ``<db>.v_charge_attempts``; ``run_sql_checks`` returns violation counts per check.
Invariants
* Tables are partitioned by ``run_id``; loading is idempotent with ``replace=True`` (drop
  partition, reload) and refused otherwise if the run already exists.
* After load, ``count()`` must equal the manifest's event count or the loader raises.
* ``flatten`` is pure; the ``data`` column holds the exact event line as generated.
Failure modes: ``RuntimeError`` on count mismatch / pre-existing run; client errors propagate.
``clickhouse_connect`` is imported lazily so unit tests and generation need no dependency.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

_RUN_ID = re.compile(r"^run_[0-9a-f]{16}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

EVENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "LowCardinality(String)"),
    ("seq", "UInt64"),
    ("id", "String"),
    ("type", "LowCardinality(String)"),
    ("created", "DateTime('UTC')"),
    ("api_version", "LowCardinality(String)"),
    ("object_type", "LowCardinality(String)"),
    ("object_id", "String"),
    ("request_id", "Nullable(String)"),
    ("idempotency_key", "Nullable(String)"),
    ("customer_id", "String"),
    ("payment_method_id", "String"),
    ("payment_intent_id", "String"),
    ("charge_id", "String"),
    ("refund_id", "String"),
    ("invoice_id", "String"),
    ("subscription_id", "String"),
    ("channel", "LowCardinality(String)"),
    ("psp", "LowCardinality(String)"),
    ("customer_country", "LowCardinality(String)"),
    ("issuer_country", "LowCardinality(String)"),
    ("payment_method_type", "LowCardinality(String)"),
    ("card_brand", "LowCardinality(String)"),
    ("platform", "LowCardinality(String)"),
    ("app_version", "LowCardinality(String)"),
    ("currency", "LowCardinality(String)"),
    ("amount", "Int64"),
    ("amount_refunded", "Int64"),
    ("status", "LowCardinality(String)"),
    ("failure_code", "LowCardinality(String)"),
    ("decline_code", "LowCardinality(String)"),
    ("risk_level", "LowCardinality(String)"),
    ("risk_score", "Int16"),
    ("reason", "LowCardinality(String)"),
    ("billing_reason", "LowCardinality(String)"),
    ("attempt_count", "UInt16"),
    ("data", "String CODEC(ZSTD(3))"),
)
EVENT_COLUMN_NAMES = tuple(c for c, _ in EVENT_COLUMNS)

TRUTH_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "LowCardinality(String)"),
    ("scenario_id", "LowCardinality(String)"),
    ("scenario_kind", "LowCardinality(String)"),
    ("record_key", "String"),
    ("incident_id", "Nullable(String)"),
    ("start", "DateTime('UTC')"),
    ("end", "Nullable(DateTime('UTC'))"),
    ("true_cause", "LowCardinality(String)"),
    ("expected_route", "LowCardinality(String)"),
    ("severity", "LowCardinality(String)"),
    ("root_cause", "String"),
    ("affected_cohorts", "String"),
    ("control_cohorts", "String"),
    ("expected_metric_effect", "String"),
    ("expected_impact", "String"),
    ("detection_window_start", "Nullable(DateTime('UTC'))"),
    ("detection_window_end", "Nullable(DateTime('UTC'))"),
    ("expected_recovery", "Nullable(DateTime('UTC'))"),
    ("unrelated_to", "Array(String)"),
    ("scenario_seed", "UInt64"),
    ("spec_hash", "String"),
    ("generator_version", "LowCardinality(String)"),
    ("record", "String"),
)

RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "String"),
    ("seed", "UInt64"),
    ("preset", "LowCardinality(String)"),
    ("generator_version", "LowCardinality(String)"),
    ("events", "UInt64"),
    ("events_sha256", "String"),
    ("truth_digest", "String"),
    ("window_start", "DateTime('UTC')"),
    ("window_end", "DateTime('UTC')"),
    ("manifest", "String"),
)


def _cols(cols) -> str:
    return ",\n    ".join(f"`{n}` {t}" for n, t in cols)


def ddl(db: str, truth_db: str) -> list[str]:
    for name in (db, truth_db):
        if not _IDENT.match(name):
            raise ValueError(f"invalid database name {name!r}")
    return [
        f"CREATE DATABASE IF NOT EXISTS {db}",
        f"CREATE DATABASE IF NOT EXISTS {truth_db}",
        f"""CREATE TABLE IF NOT EXISTS {db}.events (
    {_cols(EVENT_COLUMNS)}
) ENGINE = MergeTree
PARTITION BY run_id
ORDER BY (run_id, type, created, id)""",
        f"""CREATE VIEW IF NOT EXISTS {db}.v_charge_attempts AS
SELECT run_id, created, toStartOfHour(created) AS hour, charge_id, payment_intent_id, customer_id, channel, psp,
       customer_country, issuer_country, payment_method_type, card_brand, platform, app_version, currency, amount,
       status = 'succeeded' AS ok, failure_code, decline_code, risk_level, risk_score
FROM {db}.events
WHERE type IN ('charge.succeeded', 'charge.failed')""",
        f"""CREATE TABLE IF NOT EXISTS {truth_db}.ground_truth (
    {_cols(TRUTH_COLUMNS)}
) ENGINE = MergeTree
PARTITION BY run_id
ORDER BY (run_id, scenario_id, record_key)""",
        f"""CREATE TABLE IF NOT EXISTS {truth_db}.runs (
    {_cols(RUN_COLUMNS)}
) ENGINE = MergeTree
PARTITION BY run_id
ORDER BY run_id""",
    ]


def _s(v: Any) -> str:
    return "" if v is None else str(v)


UNKNOWN = "unknown"
DIMENSION_COLUMNS = ("channel", "psp", "customer_country", "issuer_country", "payment_method_type", "card_brand",
                     "platform", "app_version", "currency")


def flatten(env: Mapping[str, Any], run_id: str, seq: int, raw_line: str) -> dict[str, Any]:
    """Event envelope → typed row. Pure.

    Cohort dimensions that do not apply are the explicit value ``unknown`` (DATA_MODEL
    nullability rule); id/outcome columns that do not apply are ''. Country is always named:
    ``customer_country`` vs ``issuer_country`` (OQ-4).
    """
    o = env["data"]["object"]
    kind = o.get("object", "")
    meta = o.get("metadata") or {}
    row: dict[str, Any] = {
        "run_id": run_id, "seq": seq, "id": env["id"], "type": env["type"], "created": env["created"],
        "api_version": env["api_version"], "object_type": kind, "object_id": o.get("id", ""),
        "request_id": env["request"]["id"], "idempotency_key": env["request"]["idempotency_key"],
        "customer_id": "", "payment_method_id": "", "payment_intent_id": "", "charge_id": "", "refund_id": "",
        "invoice_id": "", "subscription_id": "", "channel": _s(meta.get("channel")), "psp": _s(meta.get("psp")),
        "customer_country": _s(meta.get("customer_country")), "issuer_country": "", "payment_method_type": "",
        "card_brand": "", "platform": _s(meta.get("platform")), "app_version": _s(meta.get("app_version")),
        "currency": _s(o.get("currency")), "amount": 0, "amount_refunded": 0, "status": _s(o.get("status")),
        "failure_code": "", "decline_code": "", "risk_level": "", "risk_score": 0, "reason": "",
        "billing_reason": "", "attempt_count": 0, "data": raw_line.rstrip("\n"),
    }
    if kind == "customer":
        row.update(customer_id=o["id"], customer_country=o["address"]["country"])
    elif kind == "payment_method":
        card = o.get("card") or {}
        row.update(customer_id=o["customer"], payment_method_id=o["id"], payment_method_type=o["type"],
                   card_brand=_s(card.get("brand")),
                   issuer_country=_s(card.get("country") or (o.get(o["type"]) or {}).get("country")))
    elif kind == "payment_intent":
        err = o.get("last_payment_error") or {}
        failed = env["type"] == "payment_intent.payment_failed"
        row.update(customer_id=o["customer"], payment_method_id=o["payment_method"], payment_intent_id=o["id"],
                   charge_id=_s(o.get("latest_charge")), invoice_id=_s(o.get("invoice")), amount=o["amount"],
                   failure_code=_s(err.get("code")) if failed else "",
                   decline_code=_s(err.get("decline_code")) if failed else "",
                   reason=_s(o.get("cancellation_reason")))
    elif kind == "charge":
        pmd = o.get("payment_method_details") or {}
        out = o.get("outcome") or {}
        pm_type = _s(pmd.get("type"))
        row.update(customer_id=o["customer"], payment_method_id=o["payment_method"], payment_intent_id=o["payment_intent"],
                   charge_id=o["id"], invoice_id=_s(o.get("invoice")), payment_method_type=pm_type,
                   card_brand=_s((pmd.get("card") or {}).get("brand")),
                   issuer_country=_s((pmd.get(pm_type) or {}).get("country")), amount=o["amount"],
                   amount_refunded=o.get("amount_refunded", 0), failure_code=_s(o.get("failure_code")),
                   decline_code=_s(out.get("reason")), risk_level=_s(out.get("risk_level")),
                   risk_score=int(out.get("risk_score") or 0))
    elif kind == "refund":
        row.update(refund_id=o["id"], charge_id=o["charge"], payment_intent_id=o["payment_intent"], amount=o["amount"],
                   reason=_s(o.get("reason")))
    elif kind == "invoice":
        row.update(customer_id=o["customer"], invoice_id=o["id"], subscription_id=o["subscription"],
                   payment_intent_id=_s(o.get("payment_intent")), amount=o["amount_due"],
                   customer_country=_s((o.get("customer_address") or {}).get("country")),
                   billing_reason=_s(o.get("billing_reason")), attempt_count=o.get("attempt_count", 0))
    elif kind == "subscription":
        price = o["items"]["data"][0]["price"]
        row.update(customer_id=o["customer"], subscription_id=o["id"], payment_method_id=o["default_payment_method"],
                   invoice_id=_s(o.get("latest_invoice")), amount=price["unit_amount"], currency=price["currency"],
                   customer_country=_s(meta.get("customer_country")))
    for col in DIMENSION_COLUMNS:
        if not row[col]:
            row[col] = UNKNOWN
    return row


def event_rows(run_dir: Path, run_id: str) -> Iterator[dict[str, Any]]:
    with gzip.open(run_dir / "events.jsonl.gz", "rt", encoding="utf-8") as f:
        for seq, line in enumerate(f):
            yield flatten(json.loads(line), run_id, seq, line)


def truth_rows(run_dir: Path) -> list[dict[str, Any]]:
    doc = json.loads((run_dir / "ground_truth.json").read_text())
    rows = []
    for g in doc["records"]:
        dw = g.get("expected_detection_window") or {}
        rows.append({
            "run_id": doc["run_id"], "scenario_id": g["scenario_id"], "scenario_kind": g["scenario_kind"],
            "record_key": g["record_key"], "incident_id": g["incident_id"], "start": g["start"], "end": g["end"],
            "true_cause": g["true_cause"], "expected_route": g["expected_route"], "severity": g["severity"],
            "root_cause": json.dumps(g["root_cause"], sort_keys=True),
            "affected_cohorts": json.dumps(g["affected_cohorts"], sort_keys=True),
            "control_cohorts": json.dumps(g["control_cohorts"], sort_keys=True),
            "expected_metric_effect": json.dumps(g["expected_metric_effect"], sort_keys=True),
            "expected_impact": json.dumps(g["expected_impact"], sort_keys=True),
            "detection_window_start": dw.get("start"), "detection_window_end": dw.get("end"),
            "expected_recovery": g["expected_recovery"], "unrelated_to": g["unrelated_to"],
            "scenario_seed": g["scenario_seed"], "spec_hash": g["spec_hash"],
            "generator_version": g["generator_version"], "record": json.dumps(g, sort_keys=True),
        })
    return rows


def run_row(run_dir: Path) -> dict[str, Any]:
    m = json.loads((run_dir / "manifest.json").read_text())
    return {"run_id": m["run_id"], "seed": m["seed"], "preset": m["preset"], "generator_version": m["generator_version"],
            "events": m["events"], "events_sha256": m["events_sha256"], "truth_digest": m["truth_digest"],
            "window_start": m["window"]["start"], "window_end": m["window"]["end"],
            "manifest": json.dumps(m, sort_keys=True)}


def json_each_row(rows) -> bytes:
    return ("\n".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in rows) + "\n").encode("utf-8")


# ------------------------------------------------------------------------------------------ checks
def sql_checks(db: str, run_id: str) -> dict[str, str]:
    """Each query returns one integer: the number of violations (0 = pass)."""
    if not _RUN_ID.match(run_id) or not _IDENT.match(db):
        raise ValueError("invalid run_id or database")
    e = f"{db}.events"
    r = f"run_id = '{run_id}'"

    def dangling(child_filter: str, col: str, parent_type: str) -> str:
        return (f"SELECT count() FROM (SELECT DISTINCT {col} AS ref FROM {e} WHERE {r} AND {child_filter} AND {col} != '') c "
                f"LEFT ANTI JOIN (SELECT DISTINCT object_id AS ref FROM {e} WHERE {r} AND type = '{parent_type}') p USING ref")

    return {
        "duplicate_event_ids": f"SELECT count() - uniqExact(id) FROM {e} WHERE {r}",
        "payment_method→customer": dangling("object_type = 'payment_method'", "customer_id", "customer.created"),
        "payment_intent→customer": dangling("object_type = 'payment_intent'", "customer_id", "customer.created"),
        "payment_intent→payment_method": dangling("object_type = 'payment_intent'", "payment_method_id", "payment_method.attached"),
        "charge→payment_intent": dangling("object_type = 'charge'", "payment_intent_id", "payment_intent.created"),
        "refund→charge": dangling("object_type = 'refund'", "charge_id", "charge.succeeded"),
        "invoice→subscription": dangling("object_type = 'invoice'", "subscription_id", "customer.subscription.created"),
        "subscription→customer": dangling("object_type = 'subscription'", "customer_id", "customer.created"),
        "charge_amount≠pi_amount": (
            f"SELECT count() FROM (SELECT payment_intent_id, amount FROM {e} WHERE {r} AND object_type = 'charge') c "
            f"INNER JOIN (SELECT object_id AS payment_intent_id, any(amount) AS pi_amount FROM {e} WHERE {r} "
            f"AND type = 'payment_intent.created' GROUP BY object_id) p USING payment_intent_id WHERE c.amount != p.pi_amount"),
        "refunds_exceed_charge": (
            f"SELECT count() FROM (SELECT charge_id, sum(amount) AS refunded FROM {e} WHERE {r} AND type = 'refund.created' "
            f"GROUP BY charge_id) x INNER JOIN (SELECT object_id AS charge_id, any(amount) AS amt FROM {e} WHERE {r} "
            f"AND type = 'charge.succeeded' GROUP BY object_id) y USING charge_id WHERE refunded > amt"),
        "pan_like_digit_runs": f"SELECT countIf(match(data, '(^|[^0-9])[0-9]{{13,19}}([^0-9]|$)')) FROM {e} WHERE {r}",
        "ground_truth_leak": (f"SELECT countIf(position(data, 'scn_') > 0 OR position(data, 'inc_') > 0 "
                              f"OR position(data, 'fx_') > 0) FROM {e} WHERE {r}"),
    }


def run_sql_checks(query_int, db: str, run_id: str) -> dict[str, int]:
    """``query_int(sql) -> int`` abstracts the client (clickhouse_connect or chdb in tests)."""
    return {name: int(query_int(sql)) for name, sql in sql_checks(db, run_id).items()}


# ------------------------------------------------------------------------------------------ loading
@dataclass(frozen=True)
class ClickHouseTarget:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    database: str = "anomalyos"

    @property
    def truth_database(self) -> str:
        return f"{self.database}_truth"


def target_from_settings(env: Mapping[str, str] | None = None) -> ClickHouseTarget:
    """Build the target from ``anomalyos.config.load_settings`` (the only config entry point).

    Raises ``anomalyos.config.ConfigError`` on invalid configuration.
    """
    from anomalyos.config import load_settings

    ch = load_settings(env).clickhouse
    return ClickHouseTarget(host=ch.host, port=ch.http_port, username=ch.user, password=ch.password,
                            database=ch.database)


def connect(t: ClickHouseTarget):
    import clickhouse_connect  # runtime dependency, see ADR in docs/DECISIONS.md

    return clickhouse_connect.get_client(host=t.host, port=t.port, username=t.username, password=t.password)


def load_run(client, run_dir: str | Path, database: str, truth_database: str | None = None, *,
             replace: bool = False, batch_rows: int = 50_000) -> dict[str, Any]:
    run_dir = Path(run_dir)
    truth_database = truth_database or f"{database}_truth"
    rr = run_row(run_dir)
    run_id = rr["run_id"]
    if not _RUN_ID.match(run_id):
        raise ValueError(f"unexpected run_id {run_id!r}")
    for stmt in ddl(database, truth_database):
        client.command(stmt)
    existing = int(client.command(f"SELECT count() FROM {database}.events WHERE run_id = '{run_id}'"))
    if existing and not replace:
        raise RuntimeError(f"run {run_id} already loaded ({existing} events); pass replace=True to reload")
    for table in (f"{database}.events", f"{truth_database}.ground_truth", f"{truth_database}.runs"):
        client.command(f"ALTER TABLE {table} DROP PARTITION '{run_id}'")  # no-op if absent

    buf: list[dict[str, Any]] = []
    loaded = 0
    for row in event_rows(run_dir, run_id):
        buf.append(row)
        if len(buf) >= batch_rows:
            client.raw_insert(f"{database}.events", EVENT_COLUMN_NAMES, json_each_row(buf), fmt="JSONEachRow")
            loaded += len(buf)
            buf.clear()
    if buf:
        client.raw_insert(f"{database}.events", EVENT_COLUMN_NAMES, json_each_row(buf), fmt="JSONEachRow")
        loaded += len(buf)
    tr = truth_rows(run_dir)
    client.raw_insert(f"{truth_database}.ground_truth", [c for c, _ in TRUTH_COLUMNS], json_each_row(tr), fmt="JSONEachRow")
    client.raw_insert(f"{truth_database}.runs", [c for c, _ in RUN_COLUMNS], json_each_row([rr]), fmt="JSONEachRow")

    count = int(client.command(f"SELECT count() FROM {database}.events WHERE run_id = '{run_id}'"))
    if count != rr["events"]:
        raise RuntimeError(f"loaded {count} events, manifest says {rr['events']}")
    checks = run_sql_checks(lambda q: client.command(q), database, run_id)
    return {"run_id": run_id, "events": count, "truth_records": len(tr), "checks": checks}
