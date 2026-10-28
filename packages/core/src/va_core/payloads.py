"""Canonical JSON and hashes for draft payloads.

An approval is bound to the exact content a person approved (`approval.payload_sha256`): the
Platform API hashes the payload it records, and the action executor recomputes the hash before it
acts and refuses on a mismatch. Both sides must hash the same bytes, so both use these functions.
"""

from __future__ import annotations

import hashlib
import json
import re

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(payload: object) -> bytes:
    """UTF-8 JSON with sorted keys and no insignificant whitespace. NaN/Infinity are refused."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def payload_sha256(payload: object) -> str:
    """Lower-case hex SHA-256 of `canonical_json(payload)`."""
    return hashlib.sha256(canonical_json(payload)).hexdigest()
