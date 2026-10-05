"""HTTP transport to the hosted Jev model — the only module in ``jev`` allowed to do network I/O (ADR-024, ADR-039).

Not configured yet: the provider's endpoint, wire format, pinning and limits are pending access (OQ-1). Until they
are known and recorded in an ADR, every call returns a structured ``not_configured`` error, which policy routes to
``DIGEST``. When implemented: stdlib HTTP, explicit timeout, no retry, bounded sizes, the key from ``load_settings``
and never logged.
"""

from __future__ import annotations

from pulseos.jev.client import DecisionContext, JevError, JevRequest

TRANSPORT_VERSION = "http_v0_unconfigured"


class HttpJevClient:
    name = "http"

    def __init__(self, endpoint: str | None, model: str, timeout_s: float = 10.0) -> None:
        self.endpoint, self.model, self.timeout_s = endpoint, model, timeout_s

    def ask(self, request: JevRequest, ctx: DecisionContext) -> JevError:
        return JevError("not_configured", "provider wire format pending access (OQ-1)", request.request_hash, self.name)
