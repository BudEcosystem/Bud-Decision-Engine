"""Public ids: a short prefix plus a ULID (26 characters, time-sortable, 80 random bits).

    new_id("dec")  ->  "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC"
"""
from __future__ import annotations

import os
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32: no I, L, O or U
ULID_RE = r"[0-9A-HJKMNP-TV-Z]{26}"


def ulid(ms: int | None = None) -> str:
    t = int(time.time() * 1000) if ms is None else int(ms)
    n = (t << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(ALPHABET[(n >> (5 * i)) & 31] for i in range(25, -1, -1))


def new_id(prefix: str, ms: int | None = None) -> str:
    return f"{prefix}_{ulid(ms)}"


def ulid_time(id_: str) -> float:
    """Creation time (Unix seconds) encoded in an id."""
    s = id_.rsplit("_", 1)[-1][:10]
    n = 0
    for ch in s:
        n = n * 32 + ALPHABET.index(ch)
    return n / 1000
