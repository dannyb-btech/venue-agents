"""UUIDv7 primary keys (RFC 9562): time-ordered, so new rows land at the end of B-tree indexes.

Python 3.12 and Postgres 16 have no built-in v7 generator. Order is by millisecond only; IDs made in
the same millisecond are unordered among themselves, which is fine for keys (never use them as a
clock).
"""

from __future__ import annotations

import secrets
import time
from uuid import UUID

_MASK_48 = (1 << 48) - 1
_MASK_62 = (1 << 62) - 1


def uuid7() -> UUID:
    """A new random UUIDv7: 48-bit Unix ms timestamp, version 7, variant 10, 74 random bits."""
    unix_ms = time.time_ns() // 1_000_000
    rand = secrets.randbits(74)
    value = (unix_ms & _MASK_48) << 80
    value |= 0x7 << 76
    value |= (rand >> 62) << 64  # rand_a: 12 bits
    value |= 0b10 << 62
    value |= rand & _MASK_62  # rand_b: 62 bits
    return UUID(int=value)
