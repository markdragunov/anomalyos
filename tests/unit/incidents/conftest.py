"""Reuse the ClickHouse fixture of the metric tests (live server in CI, embedded chdb locally, else skip)."""

from tests.unit.metrics.conftest import ch  # noqa: F401  (fixture)
