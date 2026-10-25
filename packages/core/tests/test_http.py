from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from va_core import ExternalServiceError, RateLimitedError, UpstreamTimeoutError
from va_core.http import HttpClient, RetryPolicy, parse_retry_after

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """Scripted transport: returns (or raises) the next item per request and records requests."""

    def __init__(self, *script: httpx.Response | Exception) -> None:
        self.script = list(script)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(self, **kwargs: object) -> HttpClient:
        return HttpClient(
            "test-svc",
            base_url="https://api.example.com",
            transport=httpx.MockTransport(self.handler),
            sleep=self.sleep,
            rand=lambda: 0.5,
            clock=lambda: NOW,
            **kwargs,  # type: ignore[arg-type]
        )


def resp(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, json={"ok": status < 400})


async def test_retries_503_then_succeeds() -> None:
    rec = Recorder(resp(503), resp(503), resp(200))
    async with rec.client() as client:
        response = await client.request("GET", "/things")
    assert response.status_code == 200
    assert len(rec.requests) == 3
    # Full jitter with rand=0.5: 0.5 * 0.5 * 2**(n-1)
    assert rec.sleeps == [0.25, 0.5]


async def test_honours_retry_after_seconds() -> None:
    rec = Recorder(resp(429, {"Retry-After": "7"}), resp(200))
    async with rec.client() as client:
        assert (await client.request("GET", "/x")).status_code == 200
    assert rec.sleeps == [7.0]


async def test_honours_retry_after_http_date() -> None:
    when = format_datetime(NOW + timedelta(seconds=12), usegmt=True)
    rec = Recorder(resp(503, {"Retry-After": when}), resp(200))
    async with rec.client() as client:
        await client.request("GET", "/x")
    assert rec.sleeps == [12.0]


async def test_long_retry_after_raises_instead_of_waiting() -> None:
    rec = Recorder(resp(429, {"Retry-After": "3600"}))
    async with rec.client() as client:
        with pytest.raises(RateLimitedError) as info:
            await client.request("GET", "/x")
    assert info.value.retry_after == 3600.0
    assert info.value.retryable
    assert rec.sleeps == []


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_post_5xx_is_not_retried_and_outcome_unknown(status: int) -> None:
    rec = Recorder(resp(status), resp(200))
    async with rec.client() as client:
        with pytest.raises(ExternalServiceError) as info:
            await client.request("POST", "/send", json={"to": "x"})
    assert info.value.status == status
    assert not info.value.retryable  # callers must not resend: it may have been sent
    assert info.value.outcome_unknown
    assert len(rec.requests) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ReadError("reset")])
async def test_post_after_sending_is_not_retried_and_outcome_unknown(error: Exception) -> None:
    rec = Recorder(error, resp(200))
    async with rec.client() as client:
        with pytest.raises(ExternalServiceError) as info:
            await client.request("POST", "/send")
    assert not info.value.retryable
    assert info.value.outcome_unknown
    assert len(rec.requests) == 1


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
async def test_non_safe_methods_are_not_retried_by_default(method: str) -> None:
    # e.g. Xero creates invoices with PUT: a blind retry would duplicate them.
    rec = Recorder(resp(503), resp(200))
    async with rec.client() as client:
        with pytest.raises(ExternalServiceError):
            await client.request(method, "/invoices")
    assert len(rec.requests) == 1


async def test_explicitly_idempotent_call_is_retried() -> None:
    rec = Recorder(resp(503), resp(200))
    async with rec.client() as client:
        response = await client.request("PUT", "/settings", idempotent=True)
    assert response.status_code == 200
    assert len(rec.requests) == 2


async def test_idempotency_key_ignored_unless_api_supports_it() -> None:
    rec = Recorder(resp(502), resp(201))
    async with rec.client() as client:
        with pytest.raises(ExternalServiceError) as info:
            await client.request("POST", "/send", idempotency_key="approval-123")
    assert info.value.outcome_unknown
    assert len(rec.requests) == 1
    assert rec.requests[0].headers["Idempotency-Key"] == "approval-123"


async def test_post_with_supported_idempotency_key_is_retried_with_header() -> None:
    rec = Recorder(resp(502), resp(201))
    async with rec.client(supports_idempotency_key=True) as client:
        response = await client.request("POST", "/send", idempotency_key="approval-123")
    assert response.status_code == 201
    assert [r.headers["Idempotency-Key"] for r in rec.requests] == ["approval-123"] * 2


async def test_connect_error_is_retried_even_for_post() -> None:
    # The connection never opened, so nothing was sent.
    rec = Recorder(httpx.ConnectError("refused"), resp(200))
    async with rec.client() as client:
        assert (await client.request("POST", "/send")).status_code == 200
    assert len(rec.requests) == 2


async def test_gives_up_after_max_attempts() -> None:
    rec = Recorder(*(resp(503) for _ in range(3)))
    async with rec.client(retry=RetryPolicy(max_attempts=3)) as client:
        with pytest.raises(ExternalServiceError) as info:
            await client.request("GET", "/x")
    assert info.value.status == 503
    assert info.value.retryable
    assert len(rec.requests) == 3
    assert len(rec.sleeps) == 2


async def test_exhausted_429_raises_rate_limited() -> None:
    rec = Recorder(resp(429), resp(429))
    async with rec.client(retry=RetryPolicy(max_attempts=2)) as client:
        with pytest.raises(RateLimitedError):
            await client.request("GET", "/x")


async def test_read_timeouts_retried_then_raise() -> None:
    rec = Recorder(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    async with rec.client(retry=RetryPolicy(max_attempts=2)) as client:
        with pytest.raises(UpstreamTimeoutError):
            await client.request("GET", "/x")
    assert len(rec.requests) == 2


async def test_client_errors_are_not_retried() -> None:
    rec = Recorder(resp(404))
    async with rec.client() as client:
        with pytest.raises(ExternalServiceError) as info:
            await client.request("GET", "/missing")
    assert info.value.status == 404
    assert not info.value.retryable
    assert len(rec.requests) == 1


async def test_raise_for_status_false_returns_response() -> None:
    rec = Recorder(resp(404))
    async with rec.client() as client:
        response = await client.request("GET", "/missing", raise_for_status=False)
    assert response.status_code == 404


async def test_error_messages_do_not_leak_query_strings() -> None:
    rec = Recorder(resp(400))
    async with rec.client() as client:
        with pytest.raises(ExternalServiceError) as info:
            await client.request("GET", "/x?api_key=supersecret")
    assert "supersecret" not in str(info.value)


def test_backoff_is_bounded_by_cap() -> None:
    policy = RetryPolicy(base_delay=1.0, max_delay=5.0)
    assert policy.backoff(10, lambda: 1.0) == 5.0
    assert policy.backoff(1, lambda: 0.0) == 0.0


def test_parse_retry_after() -> None:
    assert parse_retry_after(None, NOW) is None
    assert parse_retry_after("5", NOW) == 5.0
    assert parse_retry_after("garbage", NOW) is None
    past = format_datetime(NOW - timedelta(seconds=30), usegmt=True)
    assert parse_retry_after(past, NOW) == 0.0
