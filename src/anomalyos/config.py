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
- The password and the Jev API key never appear in ``repr``/``str`` output or logs.
- Jev (ADR-039): mode ``replay`` by default; ``record`` needs owner approval; endpoint and key are optional and,
  until provider access exists (OQ-1), unused by the transport.

Failure modes: missing or empty required values, non-integer ports, ports out
of range, unknown environment names, unsafe database identifiers.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

ALLOWED_ENVS = frozenset({"local", "test", "ci"})
JEV_MODES = frozenset({"replay", "fake", "record"})
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

DEFAULTS: dict[str, str] = {
    "ANOMALYOS_ENV": "local",
    "ANOMALYOS_SEED": "42",
    "CLICKHOUSE_HOST": "127.0.0.1",
    "CLICKHOUSE_HTTP_PORT": "8123",
    "CLICKHOUSE_DB": "anomalyos",
    "CLICKHOUSE_USER": "anomalyos",
    "CLICKHOUSE_PASSWORD": "change-me-local-only",
    "JEV_MODE": "replay",
    "JEV_MODEL": "unpinned",
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
class JevSettings:
    mode: str
    model: str
    endpoint: str | None
    api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Settings:
    env: str
    seed: int
    clickhouse: ClickHouseSettings
    jev: JevSettings = JevSettings("replay", "unpinned", None)


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

    jev_mode = _get(env, "JEV_MODE")
    if jev_mode not in JEV_MODES:
        raise ConfigError(f"JEV_MODE must be one of {sorted(JEV_MODES)}, got {jev_mode!r}")

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
        jev=JevSettings(
            mode=jev_mode,
            model=_get(env, "JEV_MODEL"),
            endpoint=env.get("JEV_ENDPOINT", "").strip() or None,
            api_key=env.get("JEV_API_KEY", "").strip() or None,
        ),
    )
