"""Per-venue credentials: secret refs, tenant-bound secrets, one refresh per credential."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from va_core.credentials import (
    ConnectionInfo,
    TenantCredentials,
    TokenCache,
    TokenGrant,
)
from va_core.errors import (
    AuthorizationError,
    ConfigurationError,
    NotFoundError,
    TenantContextError,
)
from va_core.mcp.testing import LocalRefreshLock, MemoryConnectionStore, MemorySecretStore
from va_core.secrets import TenantSecrets, venue_secret_name
from va_core.tenancy import TenantContext

FAR = 10.0**12


def _ctx(tenant: UUID | None = None) -> TenantContext:
    return TenantContext(tenant_id=tenant or uuid4(), venue_id=uuid4(), stamp_id="canary-local")


class Fixture:
    def __init__(self) -> None:
        self.store = MemorySecretStore()
        self.connections = MemoryConnectionStore()
        self.tokens = TokenCache()
        self.lock = LocalRefreshLock()

    def creds(self, ctx: TenantContext, system: str = "sample") -> TenantCredentials:
        async def lookup() -> ConnectionInfo | None:
            return await self.connections.get(ctx, system)

        return TenantCredentials(
            ctx,
            system,
            lookup=lookup,
            secrets=TenantSecrets(self.store, ctx),
            tokens=self.tokens,
            refresh_lock=self.lock,
        )

    def connect(
        self,
        ctx: TenantContext,
        *,
        system: str = "sample",
        status: str = "active",
        ref: str | None = None,
        value: str | None = None,
    ) -> str:
        name = ref or venue_secret_name(ctx, system, "refresh-token")
        if value is not None:
            self.store.values[name] = value
        self.connections.add(ctx, ConnectionInfo(system=system, status=status, secret_ref=name))
        return name


def grant(token: str, refresh: str | None = None, expires_at: float = FAR) -> TokenGrant:
    return TokenGrant(
        access_token=SecretStr(token),
        expires_at=expires_at,
        refresh_token=SecretStr(refresh) if refresh else None,
    )


async def test_secret_comes_from_the_connection_ref() -> None:
    f, ctx = Fixture(), _ctx()
    f.connect(ctx, ref=venue_secret_name(ctx, "sample", "api-key"), value="key-a")
    assert (await f.creds(ctx).secret()).get_secret_value() == "key-a"


async def test_a_ref_to_another_tenants_secret_is_refused() -> None:
    f, a, b = Fixture(), _ctx(), _ctx()
    b_secret = venue_secret_name(b, "sample", "api-key")
    f.store.values[b_secret] = "key-b"
    f.connect(a, ref=b_secret)  # a bad or forged connection row
    with pytest.raises(TenantContextError):
        await f.creds(a).secret()
    assert b_secret not in f.store.reads


async def test_no_connection() -> None:
    with pytest.raises(NotFoundError):
        await Fixture().creds(_ctx()).secret()


@pytest.mark.parametrize("status", ["pending", "error", "revoked"])
async def test_inactive_connection_is_refused(status: str) -> None:
    f, ctx = Fixture(), _ctx()
    f.connect(ctx, status=status, value="rt")
    with pytest.raises(AuthorizationError):
        await f.creds(ctx).secret()


async def test_concurrent_callers_refresh_once() -> None:
    f, ctx = Fixture(), _ctx()
    f.connect(ctx, value="rt-0")
    calls = 0

    async def refresh(_info: ConnectionInfo, current: SecretStr) -> TokenGrant:
        nonlocal calls
        calls += 1
        assert current.get_secret_value() == "rt-0"
        await asyncio.sleep(0.01)
        return grant(f"token-{calls}")

    results = await asyncio.gather(*(f.creds(ctx).access_token(refresh) for _ in range(20)))
    assert calls == 1
    assert {r.get_secret_value() for r in results} == {"token-1"}
    assert f.lock.max_concurrent == 1


async def test_two_venues_of_one_tenant_get_their_own_tokens() -> None:
    """Each venue connects its own account: a cached token must never cross venues."""
    f = Fixture()
    tenant = uuid4()
    a, b = _ctx(tenant), _ctx(tenant)
    f.connect(a, value="rt-a")
    f.connect(b, value="rt-b")

    async def refresh(_info: ConnectionInfo, current: SecretStr) -> TokenGrant:
        return grant(f"access-for-{current.get_secret_value()}")

    token_a = await f.creds(a).access_token(refresh)
    token_b = await f.creds(b).access_token(refresh)
    assert token_a.get_secret_value() == "access-for-rt-a"
    assert token_b.get_secret_value() == "access-for-rt-b"


async def test_a_shared_grant_refreshes_once_for_both_systems() -> None:
    """Gmail and Drive on one Google grant: one credential, one cache entry, one refresh."""
    f, ctx = Fixture(), _ctx()
    ref = venue_secret_name(ctx, "google", "refresh-token")
    f.connect(ctx, system="gmail", ref=ref, value="rt")
    f.connect(ctx, system="drive", ref=ref)
    calls = 0

    async def refresh(_info: ConnectionInfo, _current: SecretStr) -> TokenGrant:
        nonlocal calls
        calls += 1
        return grant("shared")

    await asyncio.gather(
        f.creds(ctx, "gmail").access_token(refresh), f.creds(ctx, "drive").access_token(refresh)
    )
    assert calls == 1


async def test_revoked_connection_is_refused_even_with_a_cached_token() -> None:
    f, ctx = Fixture(), _ctx()
    f.connect(ctx, value="rt")

    async def refresh(_info: ConnectionInfo, _current: SecretStr) -> TokenGrant:
        return grant("t")

    await f.creds(ctx).access_token(refresh)
    f.connect(ctx, status="revoked")
    with pytest.raises(AuthorizationError):
        await f.creds(ctx).access_token(refresh)


async def test_rotated_refresh_token_is_stored_inside_the_lock() -> None:
    f, ctx = Fixture(), _ctx()
    ref = f.connect(ctx, value="rt-1")
    seen: list[str] = []

    async def refresh(_info: ConnectionInfo, current: SecretStr) -> TokenGrant:
        seen.append(current.get_secret_value())
        return grant(f"t{len(seen)}", refresh=f"rt-{len(seen) + 1}", expires_at=0)

    await f.creds(ctx).access_token(refresh)  # expires at once, so the next call refreshes
    assert f.store.values[ref] == "rt-2"
    await f.creds(ctx).access_token(refresh)
    assert seen == ["rt-1", "rt-2"]
    assert f.store.values[ref] == "rt-3"


async def test_expired_token_refreshes_and_invalidate_forces_refresh() -> None:
    now = [1000.0]
    f, ctx = Fixture(), _ctx()
    f.tokens = TokenCache(clock=lambda: now[0])
    f.connect(ctx, value="rt")
    calls = 0

    async def refresh(_info: ConnectionInfo, _current: SecretStr) -> TokenGrant:
        nonlocal calls
        calls += 1
        return grant(f"t{calls}", expires_at=now[0] + 3600)

    creds = f.creds(ctx)
    assert (await creds.access_token(refresh)).get_secret_value() == "t1"
    assert (await creds.access_token(refresh)).get_secret_value() == "t1"
    now[0] += 3600 - 30  # within the expiry skew
    assert (await creds.access_token(refresh)).get_secret_value() == "t2"
    await creds.invalidate()
    assert (await creds.access_token(refresh)).get_secret_value() == "t3"


async def test_refresh_token_name_must_be_read_through() -> None:
    """Key Vault caches names not ending in `-token`: a rotated token could be read stale."""
    f, ctx = Fixture(), _ctx()
    f.connect(ctx, ref=venue_secret_name(ctx, "sample", "refresh"), value="rt")

    async def refresh(_info: ConnectionInfo, _current: SecretStr) -> TokenGrant:
        raise AssertionError("unreachable")

    with pytest.raises(ConfigurationError, match="-token"):
        await f.creds(ctx).access_token(refresh)


async def test_refresh_times_out() -> None:
    f, ctx = Fixture(), _ctx()
    f.connect(ctx, value="rt")

    async def refresh(_info: ConnectionInfo, _current: SecretStr) -> TokenGrant:
        await asyncio.sleep(10)
        raise AssertionError("unreachable")

    creds = TenantCredentials(
        ctx,
        "sample",
        lookup=lambda: f.connections.get(ctx, "sample"),
        secrets=TenantSecrets(f.store, ctx),
        tokens=f.tokens,
        refresh_lock=f.lock,
        refresh_timeout=0.05,
    )
    with pytest.raises(TimeoutError):
        await creds.access_token(refresh)
