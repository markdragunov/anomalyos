"""Append-only ClickHouse storage for the incident engine (ADR-028, ADR-041 D-7).

Rows are only inserted, never updated or deleted; the current state of an incident is its latest ``incident_events``
row (view ``incidents_current``). The writer receives a client object and does no other I/O.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

from pulseos.incidents.engine import EngineResult

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TABLES = {
    "incident_events": (("run_id", "String"), ("incident_id", "String"), ("seq", "UInt32"), ("event_time", "Int64"),
                        ("status", "LowCardinality(String)"), ("actor", "LowCardinality(String)"), ("reason", "String"),
                        ("snapshot_json", "String")),
    "incident_links": (("run_id", "String"), ("incident_id", "String"), ("candidate_id", "String"), ("linked_at", "Int64"),
                       ("kind", "LowCardinality(String)"), ("evidence_json", "String")),
    "digest_items": (("run_id", "String"), ("candidate_id", "String"), ("digest_group_id", "String"), ("time", "Int64"),
                     ("decision_id", "String"), ("promoted_to", "Nullable(String)")),
}
ORDER = {"incident_events": "(run_id, incident_id, seq)", "incident_links": "(run_id, incident_id, linked_at, candidate_id)",
         "digest_items": "(run_id, digest_group_id, time, candidate_id)"}


def _db(db: str) -> str:
    if not _IDENT.match(db):
        raise ValueError(f"invalid database name {db!r}")
    return db


def ddl(db: str) -> list[str]:
    db = _db(db)
    out = [f"CREATE DATABASE IF NOT EXISTS {db}"]
    for table, cols in TABLES.items():
        body = ",\n    ".join(f"`{n}` {t}" for n, t in cols)
        out.append(f"CREATE TABLE IF NOT EXISTS {db}.{table} (\n    {body}\n) ENGINE = MergeTree\n"
                   f"PARTITION BY run_id\nORDER BY {ORDER[table]}")
    out.append(f"CREATE VIEW IF NOT EXISTS {db}.incidents_current AS SELECT run_id, incident_id, "
               f"argMax(status, seq) AS status, argMax(snapshot_json, seq) AS snapshot_json, max(seq) AS last_seq "
               f"FROM {db}.incident_events GROUP BY run_id, incident_id")
    return out


def _insert(client, db: str, table: str, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if rows:
        cols = [c for c, _ in TABLES[table]]
        block = ("\n".join(json.dumps({c: r[c] for c in cols}, separators=(",", ":"), ensure_ascii=False)
                           for r in rows) + "\n").encode()
        client.raw_insert(f"{db}.{table}", cols, block, fmt="JSONEachRow")
    return len(rows)


def append(client, db: str, run_id: str, result: EngineResult) -> dict[str, int]:
    """Insert only (append-only, ADR-028)."""
    db = _db(db)
    if not _RUN_ID.match(run_id):
        raise ValueError(f"unexpected run_id {run_id!r}")
    return {
        "incident_events": _insert(client, db, "incident_events", (dict(r, run_id=run_id) for r in result.incident_events)),
        "incident_links": _insert(client, db, "incident_links", (dict(r, run_id=run_id) for r in result.links)),
        "digest_items": _insert(client, db, "digest_items", (dict(r, run_id=run_id) for r in result.digest_items)),
    }
