"""Connecting a venue to an external system with OAuth (scaffolding; providers arrive in P0-07).

Flow (ADR-0019): `start` returns the provider's authorize URL. The provider redirects the browser to
the venue app, which posts `{code, state}` to `callback` with its own bearer token, so the tenant
still comes from the token. State is random and single use: only its hash is stored, bound to the
user, venue and system that started it, for 10 minutes. The PKCE verifier is derived from the
state with a server key, so no verifier is stored anywhere.

On success the refresh token goes to the secret store as
`venue_secret_name(ctx, system, "refresh-token")` and the venue's `connection` row points at it;
from then on only `va_core.credentials.TenantCredentials` refreshes it (ADR-0018).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from va_core.errors import ConfigurationError, NotFoundError
from va_core.secrets import SecretStore, TenantSecrets, venue_secret_name
from va_core.tenancy import TenantContext
from va_db.models import Connection, ConnectionStatus, OAuthState
from va_db.session import tenant_session
from va_platform_api.errors import OAuthStateError

STATE_TTL = timedelta(minutes=10)
STATE_KEY_SECRET = "platform-oauth-state-key"  # noqa: S105 - a secret name, not a value
"""Platform secret (32+ chars) that derives PKCE verifiers from states. Rotating it only fails
flows started in the last 10 minutes."""
REFRESH_TOKEN_KIND = "refresh-token"  # noqa: S105 - a secret name part


class OAuthGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    refresh_token: SecretStr
    scopes: tuple[str, ...] = ()


class OAuthProvider(Protocol):
    """One external system's authorization-code + PKCE flow (S256)."""

    system: str

    def authorize_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    async def exchange(self, *, code: str, code_verifier: str, redirect_uri: str) -> OAuthGrant:
        """Redeem the code. Errors must not include the code, tokens or vendor response bodies."""
        ...


@dataclass(frozen=True)
class Started:
    authorize_url: str
    expires_at: datetime


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class OAuthFlows:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: Mapping[str, OAuthProvider],
        store: SecretStore,
        redirect_uri: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._store = store
        self._key: bytes | None = None
        self._redirect_uri = redirect_uri
        self._clock = clock

    def _provider(self, system: str) -> OAuthProvider:
        provider = self._providers.get(system)
        if provider is None:
            raise NotFoundError(f"{system} can't be connected with OAuth")
        return provider

    async def _verifier(self, state: str) -> str:
        if self._key is None:
            key = (await self._store.get(STATE_KEY_SECRET)).get_secret_value()
            if len(key) < 32:
                raise ConfigurationError(f"{STATE_KEY_SECRET} must be at least 32 characters")
            self._key = key.encode()
        # 43 chars of base64url: within RFC 7636's 43-128.
        return _b64(hmac.new(self._key, state.encode(), hashlib.sha256).digest())

    async def start(self, ctx: TenantContext, system: str) -> Started:
        provider = self._provider(system)
        state = secrets.token_urlsafe(32)
        challenge = _b64(hashlib.sha256((await self._verifier(state)).encode()).digest())
        expires_at = self._clock() + STATE_TTL
        async with tenant_session(self._sessions, ctx) as session:
            session.add(
                OAuthState(
                    venue_id=ctx.venue_id,
                    user_id=UUID(str(ctx.user_id)),
                    system=system,
                    state_sha256=_sha256(state),
                    expires_at=expires_at,
                )
            )
        url = provider.authorize_url(
            state=state, code_challenge=challenge, redirect_uri=self._redirect_uri
        )
        return Started(authorize_url=url, expires_at=expires_at)

    async def complete(self, ctx: TenantContext, system: str, code: str, state: str) -> None:
        provider = self._provider(system)
        # Consume the state first: a failed exchange needs a fresh start, never a replay.
        async with tenant_session(self._sessions, ctx) as session:
            used = await session.scalar(
                update(OAuthState)
                .where(
                    OAuthState.state_sha256 == _sha256(state),
                    OAuthState.venue_id == ctx.venue_id,
                    OAuthState.user_id == UUID(str(ctx.user_id)),
                    OAuthState.system == system,
                    OAuthState.used_at.is_(None),
                    OAuthState.expires_at > self._clock(),
                )
                .values(used_at=func.now())
                .returning(OAuthState.id)
            )
        if used is None:
            raise OAuthStateError("the connection attempt expired or is not yours; start again")
        grant = await provider.exchange(
            code=code, code_verifier=await self._verifier(state), redirect_uri=self._redirect_uri
        )
        secret_ref = venue_secret_name(ctx, system, REFRESH_TOKEN_KIND)
        await TenantSecrets(self._store, ctx).set(secret_ref, grant.refresh_token)
        async with tenant_session(self._sessions, ctx) as session:
            values = {
                "status": ConnectionStatus.ACTIVE.value,
                "scopes": list(grant.scopes),
                "secret_ref": secret_ref,
            }
            statement = insert(Connection).values(venue_id=ctx.venue_id, system=system, **values)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["tenant_id", "venue_id", "system"],
                    set_={**values, "updated_at": func.now()},
                )
            )
