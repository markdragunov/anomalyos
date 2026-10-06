"""Append-only ClickHouse storage for investigations (ADR-049 D-8): ``investigation_steps``,
``investigation_evidence``, ``investigation_reports``. Insert only; the writer receives a client object."""

from __future__ import annotations

import json
import re
from typing import Iterable

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TABLES = {
    "investigation_steps": (("run_id", "String"), ("investigation_id", "String"), ("seq", "UInt32"),
                            ("step_json", "String")),
    "investigation_evidence": (("run_id", "String"), ("investigation_id", "String"), ("evidence_id", "String"),
                               ("tool", "LowCardinality(String)"), ("payload_json", "String"), ("labels_json", "String"),
                               ("as_of", "Int64")),
    "investigation_reports": (("run_id", "String"), ("investigation_id", "String"), ("incident_id", "String"),
                              ("as_of", "Int64"), ("chooser", "LowCardinality(String)"),
                              ("stop_reason", "LowCardinality(String)"), ("conclusion", "LowCardinality(String)"),
                              ("hypotheses_json", "String"), ("claims_json", "String"), ("valid", "UInt8"),
                              ("errors_json", "String"), ("budget_json", "String")),
}
ORDER = {"investigation_steps": "(run_id, investigation_id, seq)",
         "investigation_evidence": "(run_id, investigation_id, evidence_id)",
         "investigation_reports": "(run_id, incident_id, investigation_id)"}


def ddl(db: str) -> list[str]:
    if not _IDENT.match(db):
        raise ValueError(f"invalid database name {db!r}")
    out = [f"CREATE DATABASE IF NOT EXISTS {db}"]
    for t, cols in TABLES.items():
        body = ",\n    ".join(f"`{n}` {ty}" for n, ty in cols)
        out.append(f"CREATE TABLE IF NOT EXISTS {db}.{t} (\n    {body}\n) ENGINE = MergeTree\nPARTITION BY run_id\n"
                   f"ORDER BY {ORDER[t]}")
    return out


def rows(run_id: str, inv, registry, report) -> dict[str, list[dict]]:
    from dataclasses import asdict
    steps = [{"run_id": run_id, "investigation_id": inv.investigation_id, "seq": i,
              "step_json": json.dumps(s, sort_keys=True, default=str)} for i, s in enumerate(inv.steps)]
    ev = [{"run_id": run_id, "investigation_id": inv.investigation_id, "evidence_id": e.evidence_id, "tool": e.tool,
           "payload_json": json.dumps(e.payload, sort_keys=True, default=str),
           "labels_json": json.dumps(e.labels, sort_keys=True), "as_of": e.as_of} for e in registry.items.values()]
    rep = [{"run_id": run_id, "investigation_id": inv.investigation_id, "incident_id": inv.incident_id, "as_of": inv.as_of,
            "chooser": inv.chooser, "stop_reason": inv.stop_reason, "conclusion": inv.conclusion,
            "hypotheses_json": json.dumps([asdict(h) for h in inv.hypotheses], sort_keys=True),
            "claims_json": json.dumps([asdict(c) for c in report.claims], sort_keys=True), "valid": int(report.valid),
            "errors_json": json.dumps(report.errors), "budget_json": json.dumps(inv.budget, sort_keys=True)}]
    return {"investigation_steps": steps, "investigation_evidence": ev, "investigation_reports": rep}


def append(client, db: str, run_id: str, inv, registry, report) -> dict[str, int]:
    """Insert only (append-only, ADR-028 / ADR-049)."""
    if not _IDENT.match(db) or not _RUN_ID.match(run_id):
        raise ValueError("invalid database or run id")
    out = {}
    for table, data in rows(run_id, inv, registry, report).items():
        if data:
            cols = [c for c, _ in TABLES[table]]
            block = ("\n".join(json.dumps({c: r[c] for c in cols}, separators=(",", ":")) for r in data) + "\n").encode()
            client.raw_insert(f"{db}.{table}", cols, block, fmt="JSONEachRow")
        out[table] = len(data)
    return out
