import dataclasses

import pytest

from pulseos.config import DEFAULTS, ConfigError, load_settings


def test_defaults_load():
    s = load_settings({})
    assert s.env == "local"
    assert s.seed == 42
    assert s.clickhouse.http_url == "http://127.0.0.1:8123"
    assert s.clickhouse.database == "anomalyos"


def test_overrides_apply():
    s = load_settings({"PULSEOS_ENV": "ci", "CLICKHOUSE_HTTP_PORT": "18123", "PULSEOS_SEED": "7"})
    assert (s.env, s.seed, s.clickhouse.http_port) == ("ci", 7, 18123)


def test_is_pure_and_deterministic():
    env = dict(DEFAULTS)
    assert load_settings(env) == load_settings(dict(env))


def test_settings_are_immutable():
    s = load_settings({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.seed = 1  # type: ignore[misc]


def test_password_never_in_repr():
    s = load_settings({"CLICKHOUSE_PASSWORD": "super-secret-value"})
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in str(s.clickhouse)


@pytest.mark.parametrize(
    ("key", "value", "fragment"),
    [
        ("PULSEOS_ENV", "production", "PULSEOS_ENV"),
        ("CLICKHOUSE_HTTP_PORT", "abc", "integer"),
        ("CLICKHOUSE_HTTP_PORT", "0", "CLICKHOUSE_HTTP_PORT"),
        ("CLICKHOUSE_HTTP_PORT", "70000", "CLICKHOUSE_HTTP_PORT"),
        ("PULSEOS_SEED", "-1", "PULSEOS_SEED"),
        ("CLICKHOUSE_DB", "db; DROP TABLE x", "safe identifier"),
        ("CLICKHOUSE_USER", "   ", "must not be empty"),
    ],
)
def test_invalid_values_raise_named_error(key, value, fragment):
    with pytest.raises(ConfigError, match=fragment):
        load_settings({key: value})


def test_jev_settings_default_to_replay_and_hide_the_key():
    from pulseos.config import ConfigError, load_settings
    s = load_settings({})
    assert s.jev.mode == "replay" and s.jev.endpoint is None and s.jev.api_key is None
    k = load_settings({"JEV_MODE": "fake", "JEV_API_KEY": "sk-secret-123", "JEV_ENDPOINT": "https://example.invalid"})
    assert k.jev.api_key == "sk-secret-123" and "sk-secret-123" not in repr(k)
    try:
        load_settings({"JEV_MODE": "live"})
    except ConfigError as e:
        assert "JEV_MODE" in str(e)
    else:
        raise AssertionError("unknown JEV_MODE accepted")
