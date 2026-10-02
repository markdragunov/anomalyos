from anomalyos.__main__ import main


def test_doctor_invalid_config_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("ANOMALYOS_ENV", "production")
    assert main(["doctor"]) == 2
    assert "INVALID" in capsys.readouterr().err


def test_doctor_unreachable_exits_1(monkeypatch):
    monkeypatch.setenv("ANOMALYOS_ENV", "test")
    monkeypatch.setenv("CLICKHOUSE_HOST", "127.0.0.1")
    monkeypatch.setenv("CLICKHOUSE_HTTP_PORT", "1")  # nothing listens on port 1
    assert main(["doctor"]) == 1
