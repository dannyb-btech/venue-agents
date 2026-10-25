import time

from va_db.ids import uuid7


def test_version_and_variant() -> None:
    value = uuid7()
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_embeds_current_unix_milliseconds() -> None:
    before = time.time_ns() // 1_000_000
    value = uuid7()
    after = time.time_ns() // 1_000_000
    assert before <= value.int >> 80 <= after


def test_orders_by_time_and_is_unique() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first < second
    assert len({uuid7() for _ in range(10_000)}) == 10_000
