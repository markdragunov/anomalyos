"""Test-only adapter: exposes the two clickhouse_connect methods the loader uses on top of chdb.

chdb embeds the real ClickHouse engine in-process, so DDL, inserts and the SQL checks run on
genuine ClickHouse semantics without a server. Not shipped as a runtime dependency.
"""

from __future__ import annotations

import os
import tempfile

from anomalyos.events import normalize as nz
from anomalyos.simulation import clickhouse_load as chl

_TYPES = {}
for cols in (chl.EVENT_COLUMNS, chl.TRUTH_COLUMNS, chl.RUN_COLUMNS, nz.NORM_COLUMNS):
    for name, typ in cols:
        _TYPES.setdefault(name, typ.split(" CODEC")[0])


class ChdbClient:
    def __init__(self, path: str):
        from chdb import session

        self.s = session.Session(path)
        self._tmp = tempfile.mkdtemp()

    def command(self, sql: str):
        out = self.s.query(sql, "TabSeparated")
        text = out.bytes().decode().strip() if hasattr(out, "bytes") else str(out).strip()
        if text.isdigit():
            return int(text)
        return text

    def raw_insert(self, table, column_names, insert_block: bytes, fmt: str = "JSONEachRow"):
        path = os.path.join(self._tmp, "block.jsonl")
        with open(path, "wb") as f:
            f.write(insert_block)
        cols = list(column_names)
        types = {"ground_truth": chl.TRUTH_COLUMNS, "runs": chl.RUN_COLUMNS, "events": chl.EVENT_COLUMNS,
                 "events_norm": nz.NORM_COLUMNS}[table.split(".")[1]]
        tmap = {n: t.split(" CODEC")[0] for n, t in types}
        structure = ", ".join(f"`{c}` {tmap[c]}" for c in cols).replace("'", "\\'")
        col_list = ", ".join(f"`{c}`" for c in cols)
        self.s.query(f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM file('{path}', '{fmt}', '{structure}')")

    def close(self):
        self.s.close()
