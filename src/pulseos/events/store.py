"""Write the normalized layer to ClickHouse and verify it (ADR-022 A, ADR-025).

Why: ``events_norm`` is the only table metrics, detection, cohorts and evaluation read.
Input: a Stage 1 run directory (``events.jsonl.gz`` in stream order, ``manifest.json``); the raw
``events`` table must already be loaded (``pulseos-sim load``) so referential checks can run.
Output: ``<db>.events_norm`` (MergeTree, ``PARTITION BY run_id``), loaded idempotently per run.
Invariants: reload = drop the run's partition + insert; every check below must return 0.
Failure modes: unmapped raw type or shape (``NormalizationError``), raw events missing in
ClickHouse (check fails), run already loaded without ``replace``.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any, Iterator

from pulseos.events.normalize import (
    DIMENSION_COLUMNS, MAPPED_RAW_TYPES, NORM_COLUMN_NAMES, NORM_COLUMNS, NORMALIZATION_VERSION, normalize,
)

_RUN_ID = re.compile(r"^run_[0-9a-f]{16}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
ATTEMPT_EVENT_TYPES = ("payment.authorized", "payment.declined", "payment.failed")


def _cols() -> str:
    return ",\n    ".join(f"`{n}` {t}" for n, t in NORM_COLUMNS)


def ddl(db: str) -> list[str]:
    if not _IDENT.match(db):
        raise ValueError(f"invalid database name {db!r}")
    return [
        f"CREATE DATABASE IF NOT EXISTS {db}",
        f"""CREATE TABLE IF NOT EXISTS {db}.events_norm (
    {_cols()}
) ENGINE = MergeTree
PARTITION BY run_id
ORDER BY (run_id, event_type, occurred_at, event_id)""",
    ]


def run_id_of(run_dir: Path) -> str:
    run_id = json.loads((Path(run_dir) / "manifest.json").read_text())["run_id"]
    if not _RUN_ID.match(run_id):
        raise ValueError(f"unexpected run_id {run_id!r}")
    return run_id


def _raw_stream(run_dir: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with gzip.open(Path(run_dir) / "events.jsonl.gz", "rt", encoding="utf-8") as f:
        for seq, line in enumerate(f):
            yield seq, json.loads(line)


def norm_rows(run_dir: Path, run_id: str) -> Iterator[dict[str, Any]]:
    return normalize(_raw_stream(run_dir), run_id)


def _json_each_row(rows) -> bytes:
    return ("\n".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in rows) + "\n").encode("utf-8")


def norm_sql_checks(db: str, run_id: str) -> dict[str, str]:
    """Each query returns one integer: the number of violations (0 = pass)."""
    if not _RUN_ID.match(run_id) or not _IDENT.match(db):
        raise ValueError("invalid run_id or database")
    n, e, r = f"{db}.events_norm", f"{db}.events", f"run_id = '{run_id}'"
    mapped = ", ".join(f"'{t}'" for t in sorted(MAPPED_RAW_TYPES))
    attempts = ", ".join(f"'{t}'" for t in ATTEMPT_EVENT_TYPES)
    empty = " OR ".join(f"`{d}` = ''" for d in DIMENSION_COLUMNS)
    return {
        "norm_row_without_raw_event": (
            f"SELECT count() FROM (SELECT raw_event_id AS id FROM {n} WHERE {r}) x "
            f"LEFT ANTI JOIN (SELECT DISTINCT id FROM {e} WHERE {r}) y USING id"),
        "mapped_raw_type_without_norm_row": (
            f"SELECT count() FROM (SELECT id FROM {e} WHERE {r} AND type IN ({mapped})) x "
            f"LEFT ANTI JOIN (SELECT DISTINCT raw_event_id AS id FROM {n} WHERE {r}) y USING id"),
        "norm_row_from_unmapped_raw_type": (
            f"SELECT count() FROM (SELECT raw_event_id AS id FROM {n} WHERE {r}) x "
            f"INNER JOIN (SELECT id FROM {e} WHERE {r} AND type NOT IN ({mapped})) y USING id"),
        "empty_string_dimensions": f"SELECT countIf({empty}) FROM {n} WHERE {r}",
        "duplicate_event_ids": f"SELECT count() - uniqExact(event_id) FROM {n} WHERE {r}",
        "ingested_before_occurred": f"SELECT countIf(ingested_at < occurred_at) FROM {n} WHERE {r}",
        "attempt_event_without_attempt_no": f"SELECT countIf(event_type IN ({attempts}) AND attempt_no = 0) FROM {n} WHERE {r}",
        "authorized_not_equal_succeeded_charges": (
            f"SELECT abs((SELECT count() FROM {n} WHERE {r} AND event_type = 'payment.authorized') - "
            f"(SELECT count() FROM {e} WHERE {r} AND type = 'charge.succeeded'))"),
    }


def run_norm_checks(query_int, db: str, run_id: str) -> dict[str, int]:
    """``query_int(sql) -> int`` abstracts the client (clickhouse_connect or chdb in tests)."""
    return {name: int(query_int(sql)) for name, sql in norm_sql_checks(db, run_id).items()}


def load_norm(client, run_dir: str | Path, database: str, *, replace: bool = False, batch_rows: int = 50_000) -> dict[str, Any]:
    run_dir = Path(run_dir)
    run_id = run_id_of(run_dir)
    for stmt in ddl(database):
        client.command(stmt)
    has_raw_table = int(client.command(f"SELECT count() FROM system.tables WHERE database = '{database}' AND name = 'events'"))
    raw = int(client.command(f"SELECT count() FROM {database}.events WHERE run_id = '{run_id}'")) if has_raw_table else 0
    if raw == 0:
        raise RuntimeError(f"raw events of {run_id} are not loaded; run `pulseos-sim load` first")
    existing = int(client.command(f"SELECT count() FROM {database}.events_norm WHERE run_id = '{run_id}'"))
    if existing and not replace:
        raise RuntimeError(f"run {run_id} already normalized ({existing} rows); pass replace=True to reload")
    client.command(f"ALTER TABLE {database}.events_norm DROP PARTITION '{run_id}'")  # no-op if absent

    buf: list[dict[str, Any]] = []
    loaded = 0
    for row in norm_rows(run_dir, run_id):
        buf.append(row)
        if len(buf) >= batch_rows:
            client.raw_insert(f"{database}.events_norm", NORM_COLUMN_NAMES, _json_each_row(buf), fmt="JSONEachRow")
            loaded += len(buf)
            buf.clear()
    if buf:
        client.raw_insert(f"{database}.events_norm", NORM_COLUMN_NAMES, _json_each_row(buf), fmt="JSONEachRow")
        loaded += len(buf)
    count = int(client.command(f"SELECT count() FROM {database}.events_norm WHERE run_id = '{run_id}'"))
    if count != loaded:
        raise RuntimeError(f"loaded {loaded} normalized rows, table has {count}")
    checks = run_norm_checks(lambda q: client.command(q), database, run_id)
    return {"run_id": run_id, "normalization_version": NORMALIZATION_VERSION, "raw_events": raw, "norm_rows": count, "checks": checks}
