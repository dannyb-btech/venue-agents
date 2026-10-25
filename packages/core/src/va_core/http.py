"""HTTP client for external systems: timeouts always, bounded retries, `Retry-After` respected.

Retries happen only when repeating the request is safe:
- safe methods (GET, HEAD, OPTIONS), or
- a call the caller marks `idempotent=True` (documented per connector endpoint: some APIs, e.g.
  Xero, create records with PUT, so PUT/DELETE are not assumed idempotent), or
- a call with an `idempotency_key` on a client built with `supports_idempotency_key=True` (only for
  APIs that honour the header; the key must come from the approval/action ID), or
- a connection that never opened (connect error / connect timeout), so nothing was sent.

Anything else is sent once. If it may have reached the server (timeout, transport error, 5xx), the
error has `retryable=False, outcome_unknown=True`: the action executor must never double-send.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Self

import httpx

from va_core.errors import ExternalServiceError, RateLimitedError, UpstreamTimeoutError
from va_core.redaction import redact_url
from va_core.settings import CoreSettings
from va_core.telemetry import get_logger

logger = get_logger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 20.0
    # A Retry-After longer than this raises RateLimitedError instead of blocking the caller.
    max_retry_after: float = 60.0

    def backoff(self, attempt: int, rand: Callable[[], float]) -> float:
        """Full-jitter exponential backoff for the given 1-based attempt."""
        return rand() * min(self.max_delay, self.base_delay * 2 ** (attempt - 1))


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds to wait from a `Retry-After` header (delta-seconds or HTTP-date), or None."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def _now() -> datetime:
    return datetime.now(UTC)


class HttpClient:
    """Async HTTP client for one external `service` (used in errors and logs)."""

    def __init__(
        self,
        service: str,
        *,
        base_url: str = "",
        timeout: httpx.Timeout | None = None,
        retry: RetryPolicy | None = None,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        supports_idempotency_key: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.service = service
        self.supports_idempotency_key = supports_idempotency_key
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._rand = rand
        self._clock = clock
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout or httpx.Timeout(30.0, connect=5.0),
            headers=headers,
            transport=transport,
        )

    @classmethod
    def from_settings(
        cls,
        service: str,
        settings: CoreSettings,
        *,
        base_url: str = "",
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> HttpClient:
        timeout = httpx.Timeout(settings.http_read_timeout, connect=settings.http_connect_timeout)
        return cls(
            service, base_url=base_url, timeout=timeout, headers=headers, transport=transport
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        idempotency_key: str | None = None,
        idempotent: bool = False,
        params: Mapping[str, str] | None = None,
        json: object = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        method = method.upper()
        request_headers = dict(headers or {})
        if idempotency_key is not None:
            request_headers["Idempotency-Key"] = idempotency_key
        safe_to_repeat = (
            method in SAFE_METHODS
            or idempotent
            or (idempotency_key is not None and self.supports_idempotency_key)
        )
        safe_url = redact_url(url)
        policy = self._retry

        for attempt in range(1, policy.max_attempts + 1):
            last_attempt = attempt == policy.max_attempts
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    content=content,
                    headers=request_headers,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Nothing reached the server, so a retry is safe for every method.
                if last_attempt:
                    raise UpstreamTimeoutError(
                        f"{self.service}: could not connect for {method} {safe_url}",
                        service=self.service,
                    ) from exc
                await self._wait(attempt, None, method, safe_url, type(exc).__name__)
                continue
            except httpx.TimeoutException as exc:
                if last_attempt or not safe_to_repeat:
                    raise UpstreamTimeoutError(
                        f"{self.service}: timed out on {method} {safe_url}",
                        service=self.service,
                        retryable=safe_to_repeat,
                        outcome_unknown=not safe_to_repeat,
                    ) from exc
                await self._wait(attempt, None, method, safe_url, type(exc).__name__)
                continue
            except httpx.TransportError as exc:
                if last_attempt or not safe_to_repeat:
                    raise ExternalServiceError(
                        f"{self.service}: transport error on {method} {safe_url}",
                        service=self.service,
                        retryable=safe_to_repeat,
                        outcome_unknown=not safe_to_repeat,
                    ) from exc
                await self._wait(attempt, None, method, safe_url, type(exc).__name__)
                continue

            status = response.status_code
            if status not in RETRY_STATUSES:
                if raise_for_status and status >= 400:
                    raise ExternalServiceError(
                        f"{self.service}: {method} {safe_url} returned {status}",
                        service=self.service,
                        status=status,
                    )
                return response

            retry_after = parse_retry_after(response.headers.get("Retry-After"), self._clock())
            if retry_after is not None and retry_after > policy.max_retry_after:
                await response.aclose()
                raise RateLimitedError(
                    f"{self.service}: asked to wait {retry_after:.0f}s on {method} {safe_url}",
                    service=self.service,
                    retry_after=retry_after,
                )
            if last_attempt or not safe_to_repeat:
                if not raise_for_status:
                    return response
                await response.aclose()
                if status == 429:
                    raise RateLimitedError(
                        f"{self.service}: rate limited on {method} {safe_url}",
                        service=self.service,
                        retry_after=retry_after,
                    )
                # A 5xx on a request we may not repeat: it may have taken effect.
                raise ExternalServiceError(
                    f"{self.service}: {method} {safe_url} returned {status}",
                    service=self.service,
                    status=status,
                    retryable=safe_to_repeat,
                    outcome_unknown=not safe_to_repeat,
                )
            await response.aclose()
            await self._wait(attempt, retry_after, method, safe_url, str(status))

        raise AssertionError("unreachable")  # pragma: no cover

    async def _wait(
        self, attempt: int, retry_after: float | None, method: str, safe_url: str, reason: str
    ) -> None:
        delay = retry_after if retry_after is not None else self._retry.backoff(attempt, self._rand)
        logger.warning(
            "%s: retrying %s %s after %s in %.2fs (attempt %d)",
            self.service,
            method,
            safe_url,
            reason,
            delay,
            attempt,
        )
        await self._sleep(delay)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
