"""Deterministic identifiers and seed derivation for the synthetic world.

Why: every object and event ID, and every random stream, must be a pure function of the world
seed plus a *structural key* (what the thing is), never of a global counter. That gives:

* reproducibility — same seed + same spec → byte-identical dataset;
* locality — adding or changing a scenario perturbs only the IDs and random draws inside the
  cohorts/windows it touches; the rest of the world stays byte-identical, which is what lets us
  compare a scenario run against its counterfactual.

Input:  integer seed and a tuple of key parts (str/int).
Output: Stripe-shaped IDs (``prefix_`` + base62) and 64-bit derived seeds.
Invariants: no dependency on ``hash()`` (salted per process), wall-clock time, or global state.
Failure modes: none at runtime; collisions are negligible (128-bit digest, 24 base62 chars).
"""

from __future__ import annotations

import hashlib

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SEP = b"\x1f"


def _digest(parts: tuple[object, ...], size: int = 16) -> bytes:
    h = hashlib.blake2b(digest_size=size)
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(_SEP)
    return h.digest()


def derive_seed(seed: int, *parts: object) -> int:
    """64-bit seed for an independent random stream identified by ``parts``."""
    return int.from_bytes(_digest((seed, *parts), 8), "big")


def stable_id(prefix: str, seed: int, *parts: object, length: int = 24) -> str:
    """Stripe-shaped ID, e.g. ``pi_3Kq...``. Not claimed to match Stripe's internal format."""
    n = int.from_bytes(_digest((prefix, seed, *parts), 16), "big")
    chars = []
    for _ in range(length):
        n, r = divmod(n, 62)
        chars.append(_ALPHABET[r])
    return f"{prefix}_{''.join(chars)}"


def short_hash(*parts: object) -> str:
    """Hex fingerprint for manifests/specs (not an ID)."""
    return _digest(parts, 16).hex()
