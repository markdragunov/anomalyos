"""Operational context (ADR-049 D-3a): typed, read-only access to deployments and PSP status entries.

The investigation agent's ``get_deployments`` and ``check_psp_status`` tools read only through these functions:
static SQL, bound parameters, closed sets of services / PSPs, and as-of rules (an entry exists for a decision only if
it was deployed or posted by ``as_of``; a resolution is visible only if it happened by ``as_of``).
"""
