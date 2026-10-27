"""Per-venue credentials for external systems (connectors, sync workers, executor).

Credentials belong to a venue's `connection` row, which names its secret (`secret_ref`); the
secret is read through `TenantSecrets`, so a reference to another tenant's secret is refused.

OAuth access tokens are cached in memory per credential: (tenant, secret ref). Every call first
re-reads the connection, so a disconnected venue stops getting tokens at once and two venues with
their own grants never share one. Refreshing is serialised per credential: an in-process lock, then
a cross-replica `RefreshLock` (a Postgres advisory lock in `va_db.connectors`). Inside both, the
framework reads the current refresh token (uncached: its name must end in `-token`, see
`KeyVaultSecretStore`), calls the vendor-specific `refresh`, and stores a rotated refresh token
before releasing, so no replica refreshes with a token another one already rotated. Everything that
refreshes a credential (connectors, sync workers, the OAuth callback) must go through this path.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr

from va_core.errors import AuthorizationError, ConfigurationError, NotFoundError
from va_core.secrets import TenantSecrets
from va_core.tenancy import TenantContext

ACTIVE = "active"
EXPIRY_SKEW = 60.0
"""Seconds before expiry at which a cached access token counts as expired."""
REFRESH_TIMEOUT = 30.0
UNCACHED_SUFFIX = "-token"


class ConnectionInfo(BaseModel):
    """The `connection` row fields a connector needs. `secret_ref` names a secret, not a value."""

    model_config = ConfigDict(frozen=True)

    system: str
    status: str
    scopes: tuple[str, ...] = ()
    secret_ref: str | None = None


class ConnectionStore(Protocol):
    """The connection of `ctx`'s venue to `system` (under RLS for `ctx`'s tenant)."""

    async def get(self, ctx: TenantContext, system: str) -> ConnectionInfo | None: ...


class RefreshLock(Protocol):
    """Held while the credential `secret_ref` of `ctx`'s tenant refreshes, across every replica."""

    def hold(self, ctx: TenantContext, secret_ref: str) -> AbstractAsyncContextManager[None]: ...


class TokenGrant(BaseModel):
    """What a vendor token endpoint returned."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    expires_at: float
    """Epoch seconds."""
    refresh_token: SecretStr | None = None
    """Set when the vendor rotated the refresh token; the framework stores it."""


type RefreshFn = Callable[[ConnectionInfo, SecretStr], Awaitable[TokenGrant]]
"""Vendor-specific: exchange the current refresh token for a grant. Must not use the database
(the refresh lock holds a pooled connection) or the secret store (the framework does that)."""


class TokenCache:
    """In-memory access tokens and refresh locks, per (tenant, secret ref). One per process.

    Grows with the credentials a replica has served (bounded by the stamp's connections).
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._tokens: dict[tuple[UUID, str], TokenGrant] = {}
        self._locks: dict[tuple[UUID, str], asyncio.Lock] = {}

    def valid(self, key: tuple[UUID, str]) -> SecretStr | None:
        grant = self._tokens.get(key)
        if grant is not None and grant.expires_at - EXPIRY_SKEW > self._clock():
            return grant.access_token
        return None

    def put(self, key: tuple[UUID, str], grant: TokenGrant) -> None:
        # The cache never needs the refresh token.
        self._tokens[key] = grant.model_copy(update={"refresh_token": None})

    def drop(self, key: tuple[UUID, str]) -> None:
        self._tokens.pop(key, None)

    def lock(self, key: tuple[UUID, str]) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())


class TenantCredentials:
    """Credentials for one venue's connection to one system, for the duration of one call.

    `lookup` is bound to the caller's tenant context, so this object can't be pointed elsewhere.
    """

    def __init__(
        self,
        ctx: TenantContext,
        system: str,
        *,
        lookup: Callable[[], Awaitable[ConnectionInfo | None]],
        secrets: TenantSecrets,
        tokens: TokenCache,
        refresh_lock: RefreshLock,
        refresh_timeout: float = REFRESH_TIMEOUT,
    ) -> None:
        self._ctx = ctx
        self._system = system
        self._lookup = lookup
        self._secrets = secrets
        self._tokens = tokens
        self._refresh_lock = refresh_lock
        self._refresh_timeout = refresh_timeout

    async def connection(self) -> ConnectionInfo:
        """The venue's active connection to this system, or an error."""
        info = await self._lookup()
        if info is None:
            raise NotFoundError(f"venue has no {self._system} connection")
        if info.status != ACTIVE:
            raise AuthorizationError(f"the {self._system} connection is not active")
        return info

    async def secret(self) -> SecretStr:
        """The secret the connection references (an API key). Another tenant's is refused."""
        return await self._secrets.get(self._secret_ref(await self.connection()))

    async def access_token(self, refresh: RefreshFn) -> SecretStr:
        """A valid OAuth access token for this venue's connection, refreshing once if needed.

        The connection's `secret_ref` holds the refresh token and must end in `-token`.
        """
        info = await self.connection()
        ref = self._secret_ref(info)
        if not ref.endswith(UNCACHED_SUFFIX):
            raise ConfigurationError(
                f"the {self._system} refresh token's secret name must end in {UNCACHED_SUFFIX!r}"
            )
        key = (self._ctx.tenant_id, ref)
        cached = self._tokens.valid(key)
        if cached is not None:
            return cached
        async with self._tokens.lock(key):
            cached = self._tokens.valid(key)
            if cached is not None:
                return cached
            async with (
                asyncio.timeout(self._refresh_timeout),
                self._refresh_lock.hold(self._ctx, ref),
            ):
                current = await self._secrets.get(ref)
                grant = await refresh(info, current)
                if grant.refresh_token is not None and (
                    grant.refresh_token.get_secret_value() != current.get_secret_value()
                ):
                    await self._secrets.set(ref, grant.refresh_token)
            self._tokens.put(key, grant)
            return grant.access_token

    async def invalidate(self) -> None:
        """Forget the cached access token (e.g. after the vendor answered 401)."""
        info = await self.connection()
        self._tokens.drop((self._ctx.tenant_id, self._secret_ref(info)))

    def _secret_ref(self, info: ConnectionInfo) -> str:
        if info.secret_ref is None:
            raise NotFoundError(f"the {self._system} connection has no secret reference")
        return info.secret_ref
