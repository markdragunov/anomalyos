"""Typed, validated runtime configuration.

Why: every later layer (ingestion, metrics, detection) needs the same
connection and determinism settings, and misconfiguration must fail loudly at
startup rather than as a confusing error deep inside a query.

Input:  a mapping of environment variables (defaults to ``os.environ``).
Output: an immutable ``Settings`` object.

Invariants:
- ``load_settings`` is pure: same mapping in → same Settings out. It never
  reads files, the network, or the clock. (No .env auto-loading: that would
  make configuration depend on the working directory.)
- Invalid values raise ``ConfigError`` naming the offending variable.
- The password never appears in ``repr``/``str`` output or logs.

Failure modes: missing or empty required values, non-integer ports, ports out
of range, unknown environment names, unsafe database identifiers.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

ALLOWED_ENVS = frozenset({"local", "test", "ci"})
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

DEFAULTS: dict[str, str] = {
    "ANOMALYOS_ENV": "local",
    "ANOMALYOS_SEED": "42",
    "CLICKHOUSE_HOST": "127.0.0.1",
    "CLICKHOUSE_HTTP_PORT": "8123",
    "CLICKHOUSE_DB": "anomalyos",
    "CLICKHOUSE_USER": "anomalyos",
    "CLICKHOUSE_PASSWORD": "change-me-local-only",
}


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class ClickHouseSettings:
    host: str
    http_port: int
    database: str
    user: str
    password: str = field(repr=False)

    @property
    def http_url(self) -> str:
        return f"http://{self.host}:{self.http_port}"


@dataclass(frozen=True)
class Settings:
    env: str
    seed: int
    clickhouse: ClickHouseSettings


def _get(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, DEFAULTS[key]).strip()
    if not value:
        raise ConfigError(f"{key} must not be empty")
    return value


def _int(env: Mapping[str, str], key: str, *, lo: int, hi: int) -> int:
    raw = _get(env, key)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be in [{lo}, {hi}], got {value}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    env_name = _get(env, "ANOMALYOS_ENV")
    if env_name not in ALLOWED_ENVS:
        raise ConfigError(
            f"ANOMALYOS_ENV must be one of {sorted(ALLOWED_ENVS)}, got {env_name!r}"
        )

    database = _get(env, "CLICKHOUSE_DB")
    if not _IDENTIFIER.match(database):
        raise ConfigError(f"CLICKHOUSE_DB is not a safe identifier: {database!r}")

    return Settings(
        env=env_name,
        seed=_int(env, "ANOMALYOS_SEED", lo=0, hi=2**32 - 1),
        clickhouse=ClickHouseSettings(
            host=_get(env, "CLICKHOUSE_HOST"),
            http_port=_int(env, "CLICKHOUSE_HTTP_PORT", lo=1, hi=65535),
            database=database,
            user=_get(env, "CLICKHOUSE_USER"),
            password=_get(env, "CLICKHOUSE_PASSWORD"),
        ),
    )
