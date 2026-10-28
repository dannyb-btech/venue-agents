import math

import pytest

from va_core.payloads import SHA256_HEX, canonical_json, payload_sha256


def test_canonical_json_ignores_key_order_and_whitespace() -> None:
    a = {"to": "chef@example.test", "lines": [1, 2], "nested": {"b": 1, "a": "é"}}
    b = {"nested": {"a": "é", "b": 1}, "lines": [1, 2], "to": "chef@example.test"}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a).decode() == (
        '{"lines":[1,2],"nested":{"a":"é","b":1},"to":"chef@example.test"}'
    )
    assert payload_sha256(a) == payload_sha256(b)
    assert SHA256_HEX.fullmatch(payload_sha256(a))


def test_any_change_changes_the_hash() -> None:
    assert payload_sha256({"amount": "10.00"}) != payload_sha256({"amount": "10.01"})
    assert payload_sha256({"lines": [1, 2]}) != payload_sha256({"lines": [2, 1]})


def test_nan_is_refused() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})
