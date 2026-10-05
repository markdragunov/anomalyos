"""Health-check contract, tested against an in-process fake HTTP server.

No Docker, no external network: deterministic and runs anywhere.
"""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pulseos.clickhouse import check_health
from pulseos.config import ClickHouseSettings


def _settings(port: int, password: str = "pw") -> ClickHouseSettings:
    return ClickHouseSettings("127.0.0.1", port, "anomalyos", "u", password)


class _FakeClickHouse(BaseHTTPRequestHandler):
    ping_body = b"Ok.\n"
    expected_key = "pw"
    seen_paths: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).seen_paths.append(self.path)
        if self.path == "/ping":
            self._send(200, type(self).ping_body)
        elif self.headers.get("X-ClickHouse-Key") != type(self).expected_key:
            self._send(401, b"auth failed")
        else:
            self._send(200, b"25.8.1.1\n")

    def _send(self, code, body):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture
def fake_server():
    _FakeClickHouse.ping_body = b"Ok.\n"
    _FakeClickHouse.seen_paths = []
    server = HTTPServer(("127.0.0.1", 0), _FakeClickHouse)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_healthy(fake_server):
    r = check_health(_settings(fake_server.server_port))
    assert (r.ok, r.reachable, r.authenticated, r.version) == (True, True, True, "25.8.1.1")


def test_bad_credentials(fake_server):
    r = check_health(_settings(fake_server.server_port, password="wrong"))
    assert (r.ok, r.reachable, r.authenticated) == (False, True, False)
    assert "401" in r.detail


def test_unexpected_ping_body(fake_server):
    _FakeClickHouse.ping_body = b"hello"
    r = check_health(_settings(fake_server.server_port))
    assert not r.ok and "unexpected" in r.detail


def test_unreachable_returns_report_not_exception():
    r = check_health(_settings(_free_port()), timeout=0.5)
    assert (r.ok, r.reachable) == (False, False)


def test_credentials_never_in_url(fake_server):
    check_health(_settings(fake_server.server_port, password="pw"))
    assert all("pw" not in p and "X-ClickHouse" not in p for p in _FakeClickHouse.seen_paths)
