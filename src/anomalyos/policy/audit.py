"""Runtime decision audit (ADR-039 D-8, INV-010): an append-only ClickHouse table, one row per decision.

Rows are never updated; a re-decision is a new row with a new ``decided_seq``. The writer receives a client object
(clickhouse-connect or a compatible adapter) and does no other I/O. Replay artifacts (``jev.replay``) and evaluation
joins (``evaluation``) are stored elsewhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Iterable

from anomalyos.policy.decide import DecisionRecord

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
COLUMNS = (
    ("run_id", "String"), ("decided_seq", "UInt32"), ("decision_id", "String"), ("mode", "LowCardinality(String)"),
    ("candidate_id", "String"), ("as_of", "Int64"), ("evaluated_at", "Int64"), ("state_json", "String"),
    ("state_hash", "String"), ("state_schema_version", "LowCardinality(String)"),
    ("question_set_version", "LowCardinality(String)"), ("requested_model", "String"), ("returned_model", "String"),
    ("transport", "LowCardinality(String)"), ("request_hash", "String"), ("provider_request_id", "String"),
    ("answers_json", "String"), ("verified", "UInt8"), ("verifier_reasons", "Array(String)"),
    ("policy_version", "LowCardinality(String)"), ("route", "LowCardinality(String)"), ("rule", "LowCardinality(String)"),
    ("evidence_ids", "Array(String)"), ("provenance_json", "String"), ("request_at", "Int64"), ("response_at", "Int64"),
    ("actor", "LowCardinality(String)"), ("calls_used", "UInt8"), ("request_bytes", "UInt32"),
    ("incident_p", "Nullable(Float64)"), ("severity_level", "LowCardinality(String)"),
    ("category_top", "LowCardinality(String)"),
)
COLUMN_NAMES = [c for c, _ in COLUMNS]


def ddl(db: str) -> list[str]:
    if not _IDENT.match(db):
        raise ValueError(f"invalid database name {db!r}")
    cols = ",\n    ".join(f"`{n}` {t}" for n, t in COLUMNS)
    return [f"CREATE DATABASE IF NOT EXISTS {db}",
            f"CREATE TABLE IF NOT EXISTS {db}.jev_decisions (\n    {cols}\n) ENGINE = MergeTree\n"
            f"PARTITION BY run_id\nORDER BY (run_id, candidate_id, decided_seq)"]


def rows(run_id: str, records: Iterable[DecisionRecord], first_seq: int = 0) -> list[dict]:
    if not _RUN_ID.match(run_id):
        raise ValueError(f"unexpected run_id {run_id!r}")
    out = []
    for i, r in enumerate(records):
        d = asdict(r)
        d.update(run_id=run_id, decided_seq=first_seq + i, verified=int(r.verified),
                 verifier_reasons=list(r.verifier_reasons), evidence_ids=list(r.evidence_ids))
        out.append({c: d[c] for c in COLUMN_NAMES})
    return out


def append(client, db: str, run_id: str, records: Iterable[DecisionRecord], first_seq: int = 0) -> int:
    """Insert only; never ALTER, DELETE or replace (append-only, ADR-028)."""
    if not _IDENT.match(db):
        raise ValueError(f"invalid database name {db!r}")
    data = rows(run_id, records, first_seq)
    if data:
        block = ("\n".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in data) + "\n").encode()
        client.raw_insert(f"{db}.jev_decisions", COLUMN_NAMES, block, fmt="JSONEachRow")
    return len(data)
