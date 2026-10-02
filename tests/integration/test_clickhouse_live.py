"""Live check against the Docker Compose ClickHouse.

Opt-in: ANOMALYOS_RUN_INTEGRATION=1 pytest -m integration
Skipped (never silently passed) otherwise.
"""

import os

import pytest

from anomalyos.clickhouse import check_health
from anomalyos.config import load_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ANOMALYOS_RUN_INTEGRATION") != "1",
        reason="set ANOMALYOS_RUN_INTEGRATION=1 with ClickHouse running",
    ),
]


def test_live_clickhouse_is_healthy():
    report = check_health(load_settings().clickhouse, timeout=5.0)
    assert report.ok, report.detail
    assert report.version
