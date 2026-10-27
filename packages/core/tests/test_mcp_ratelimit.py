"""Connector rate limits: per tenant and per system, refusals cost nothing."""

from __future__ import annotations

from uuid import uuid4

import pytest

from va_core.errors import RateLimitedError
from va_core.mcp.ratelimit import Rate, RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_burst_then_refill() -> None:
    clock = Clock()
    limiter = RateLimiter(
        "sample", per_tenant=Rate(1.0, 2), per_system=Rate(100.0, 100), clock=clock
    )
    tenant = uuid4()
    limiter.acquire(tenant)
    limiter.acquire(tenant)
    with pytest.raises(RateLimitedError) as info:
        limiter.acquire(tenant)
    assert info.value.retry_after == pytest.approx(1.0)
    clock.now = 1.0
    limiter.acquire(tenant)


def test_one_tenant_does_not_throttle_another() -> None:
    limiter = RateLimiter(
        "sample", per_tenant=Rate(1.0, 1), per_system=Rate(100.0, 100), clock=Clock()
    )
    a, b = uuid4(), uuid4()
    limiter.acquire(a)
    with pytest.raises(RateLimitedError):
        limiter.acquire(a)
    limiter.acquire(b)


def test_system_cap_applies_across_tenants() -> None:
    clock = Clock()
    limiter = RateLimiter("sample", per_tenant=Rate(10.0, 10), per_system=Rate(1.0, 2), clock=clock)
    limiter.acquire(uuid4())
    limiter.acquire(uuid4())
    with pytest.raises(RateLimitedError):
        limiter.acquire(uuid4())


def test_refusal_does_not_spend_the_tenant_bucket() -> None:
    clock = Clock()
    limiter = RateLimiter("sample", per_tenant=Rate(1.0, 1), per_system=Rate(1.0, 1), clock=clock)
    a, b = uuid4(), uuid4()
    limiter.acquire(a)  # spends the system bucket too
    with pytest.raises(RateLimitedError):
        limiter.acquire(b)  # refused by the system bucket: b's own bucket stays full
    clock.now = 1.0
    limiter.acquire(b)


@pytest.mark.parametrize(("rate", "burst"), [(0.0, 1), (-1.0, 1), (1.0, 0)])
def test_invalid_rate(rate: float, burst: int) -> None:
    with pytest.raises(ValueError):
        Rate(rate, burst)
