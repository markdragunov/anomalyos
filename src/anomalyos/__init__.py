"""AnomalyOS — AI-native Billing Incident Intelligence (research prototype).

Stage 0 contains only the engineering foundation: typed configuration and a
ClickHouse connectivity check. Business logic (events, metrics, detection,
cohorts, reasoning, incidents, investigation) is intentionally absent; see
docs/ARCHITECTURE.md for the contracts those layers will implement.
"""

__version__ = "0.0.1"
