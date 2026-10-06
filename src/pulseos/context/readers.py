"""Readers for ``<db>.deployments`` and ``<db>.psp_status`` (ADR-049 D-3a). Read-only; no ground truth.

Input: a query runner (``rows(sql, params)``), database, run id, a closed set of services or PSPs, a window and
``as_of`` (epoch seconds). Output: bounded lists of typed entries visible at ``as_of``.
Failure modes: an unknown service or PSP or an unsafe database name raises ``ValueError``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SERVICES = ("api_gateway", "checkout_web", "renewal_job", "dunning_service", "refund_service", "pricing_service",
            "ledger", "mobile_ios", "mobile_android")
PSPS = ("psp_alpha", "psp_beta", "psp_gamma")
MAX_ROWS = 50
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class Deployment:
    id: str
    service: str
    version: str
    deployed_at: int


@dataclass(frozen=True)
class StatusEntry:
    id: str
    psp: str
    component: str
    level: str
    posted_at: int
    resolved_at: int | None  # None while unresolved at as_of


def _check(db: str, run_id: str, values, allowed, what: str) -> None:
    if not _IDENT.match(db) or not _RUN_ID.match(run_id):
        raise ValueError("invalid database or run id")
    bad = sorted(set(values) - set(allowed))
    if bad or not values:
        raise ValueError(f"{what} outside the closed set: {bad or 'empty'}")


def deployments(runner, db: str, run_id: str, services, start: int, end: int, as_of: int) -> list[Deployment]:
    """Deploys of ``services`` in ``[start, min(end, as_of)]``."""
    _check(db, run_id, services, SERVICES, "services")
    sql = (f"SELECT id, service, version, deployed_at FROM {db}.deployments WHERE run_id = {{run:String}} "
           f"AND service IN {{services:Array(String)}} AND deployed_at >= {{start:Int64}} AND deployed_at <= {{stop:Int64}} "
           f"ORDER BY deployed_at, id LIMIT {MAX_ROWS}")
    rows = runner.rows(sql, {"run": run_id, "services": sorted(services), "start": start, "stop": min(end, as_of)})
    return [Deployment(str(r[0]), str(r[1]), str(r[2]), int(r[3])) for r in rows]


def psp_status(runner, db: str, run_id: str, psps, start: int, end: int, as_of: int) -> list[StatusEntry]:
    """Status entries of ``psps`` posted by ``as_of`` and overlapping ``[start, end]``; a resolution after ``as_of``
    is hidden."""
    _check(db, run_id, psps, PSPS, "psps")
    sql = (f"SELECT id, psp, component, level, posted_at, resolved_at FROM {db}.psp_status WHERE run_id = {{run:String}} "
           f"AND psp IN {{psps:Array(String)}} AND posted_at <= {{as_of:Int64}} AND posted_at <= {{stop:Int64}} "
           f"AND (resolved_at IS NULL OR resolved_at >= {{start:Int64}}) ORDER BY posted_at, id LIMIT {MAX_ROWS}")
    rows = runner.rows(sql, {"run": run_id, "psps": sorted(psps), "start": start, "stop": end, "as_of": as_of})
    out = []
    for r in rows:
        resolved = None if r[5] is None or r[5] == "\\N" else int(r[5])
        out.append(StatusEntry(str(r[0]), str(r[1]), str(r[2]), str(r[3]), int(r[4]),
                               resolved if resolved is not None and resolved <= as_of else None))
    return out
