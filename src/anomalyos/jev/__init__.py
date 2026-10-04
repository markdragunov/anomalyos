"""Jev decision layer (architecture layer 6, Stage 5, ADR-039): typed state in, typed answers out, verified by code.

Jev decides; code builds the state, verifies the answers and routes (``anomalyos.policy``). Only ``transport`` may do
network I/O (INV-004 as clarified by ADR-024). Nothing here reads ground truth (INV-015).
"""
