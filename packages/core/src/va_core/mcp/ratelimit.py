"""Connector-side rate limits: per (tenant, system) and per system, before any vendor call.

In-process token buckets: each replica limits on its own, so the real ceiling is per replica.
Cross-replica limits (Postgres or Redis) are deferred (ADR-0018). Vendor 429s are still handled by
`va_core.http.HttpClient` (`Retry-After`).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from va_core.errors import RateLimitedError


@dataclass(frozen=True)
class Rate:
    per_second: float
    burst: int

    def __post_init__(self) -> None:
        if self.per_second <= 0 or self.burst < 1:
            raise ValueError("rate must be positive with a burst of at least 1")


class TokenBucket:
    def __init__(self, rate: Rate, clock: Callable[[], float]) -> None:
        self._rate = rate
        self._clock = clock
        self._tokens = float(rate.burst)
        self._updated = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(float(self._rate.burst), self._tokens + elapsed * self._rate.per_second)
        self._updated = now

    def wait_time(self) -> float:
        """Seconds until one token is available (0 when one is available now)."""
        self._refill()
        if self._tokens >= 1:
            return 0.0
        return (1 - self._tokens) / self._rate.per_second

    def take(self) -> None:
        self._refill()
        self._tokens -= 1


class RateLimiter:
    """Refuses a call when the tenant's or the system's bucket is empty."""

    def __init__(
        self,
        system: str,
        *,
        per_tenant: Rate,
        per_system: Rate,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._system = system
        self._per_tenant = per_tenant
        self._clock = clock
        self._system_bucket = TokenBucket(per_system, clock)
        self._tenant_buckets: dict[UUID, TokenBucket] = {}

    def acquire(self, tenant_id: UUID) -> None:
        """Take one call for `tenant_id`, or raise `RateLimitedError` with `retry_after`."""
        bucket = self._tenant_buckets.get(tenant_id)
        if bucket is None:
            bucket = self._tenant_buckets[tenant_id] = TokenBucket(self._per_tenant, self._clock)
        # Check both before taking from either, so a refusal costs nothing.
        wait = max(bucket.wait_time(), self._system_bucket.wait_time())
        if wait > 0:
            raise RateLimitedError(
                f"{self._system} connector rate limit reached",
                service=self._system,
                retry_after=wait,
            )
        bucket.take()
        self._system_bucket.take()
