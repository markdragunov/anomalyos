"""Minimal ClickHouse connectivity over the HTTP interface (stdlib only).

Why: Stage 0 must prove the analytical store is reachable before any schema
or ingestion work exists. A client library is deliberately NOT chosen yet
(docs/DECISIONS.md, ADR-013); this module covers only health and version.

Input:  ``ClickHouseSettings``.
Output: ``HealthReport`` — a value object, never an exception, so callers
        (CLI, tests, future readiness checks) can render it however they like.

Invariants:
- Read-only: only ``GET /ping`` and ``SELECT version()``.
- Credentials travel in HTTP headers, never in the URL.
- Every network call has an explicit timeout.

Failure modes: server down / refused, timeout, auth failure (any HTTP error on the query; ClickHouse 25.8 returns 403),
unexpected response body. All map to ``ok=False`` with a human-readable detail.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from anomalyos.config import ClickHouseSettings

DEFAULT_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    reachable: bool
    authenticated: bool
    version: str | None
    detail: str


def _request(url: str, settings: ClickHouseSettings, timeout: float) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "X-ClickHouse-User": settings.user,
            "X-ClickHouse-Key": settings.password,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local dev URL)
        return resp.read().decode("utf-8").strip()


def check_health(
    settings: ClickHouseSettings, timeout: float = DEFAULT_TIMEOUT_S
) -> HealthReport:
    base = settings.http_url

    try:
        body = _request(f"{base}/ping", settings, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return HealthReport(False, False, False, None, f"unreachable at {base}: {exc}")
    if body != "Ok.":
        return HealthReport(False, False, False, None, f"unexpected /ping body: {body!r}")

    query = urllib.parse.urlencode(
        {"query": "SELECT version()", "database": settings.database}
    )
    try:
        version = _request(f"{base}/?{query}", settings, timeout)
    except urllib.error.HTTPError as exc:
        return HealthReport(
            False, True, False, None, f"query rejected (HTTP {exc.code}); check credentials/database"
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return HealthReport(False, True, False, None, f"query failed: {exc}")

    return HealthReport(True, True, True, version, f"ClickHouse {version} at {base}")
