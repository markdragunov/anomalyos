"""Policy engine (architecture layer 7, Stage 5, ADR-039): deterministic routes from verified Jev answers.

``rules`` (policy_v1) and ``baseline`` (baseline_v1, no Jev, benchmark control only) are pure functions of their inputs
and version (INV-006). ``decide`` composes state -> client -> verifier -> route -> record; ``audit`` writes records to
an append-only ClickHouse table. Any failure routes to ``DIGEST`` (ADR-028). Nothing here reads ground truth.
"""
