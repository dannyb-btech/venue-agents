"""Who is calling: venue users with an Entra External ID access token (ADR-0019).

The token proves the user (`oid`) and carries our tenant ID in one claim. That claim is the only
source of the tenant. Roles and venue access are not in the token: `access.py` reads them from the
stamp database on every request.

Locally a dev token (HS256, same claims) stands in for Entra. It is refused outside
ENVIRONMENT=local/test and when Azure hosting variables are present.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

import jwt
from pydantic import SecretStr

from va_core.errors import AuthenticationError, ConfigurationError, VenueAgentsError
from va_core.http import HttpClient
from va_core.settings import Environment
from va_core.telemetry import get_logger
from va_platform_api.settings import PlatformApiSettings

logger = get_logger(__name__)

LEEWAY = 60
"""Seconds of clock skew allowed on `exp`/`nbf`."""
KEYS_TTL = 3600.0
REFETCH_MIN_INTERVAL = 60.0
"""An unknown `kid` refetches the key set at most this often (keys rotate rarely)."""

DEV_ISSUER = "va-dev-issuer"
DEV_AUDIENCE = "va-platform-api"
DEV_LIFETIME = 3600


@dataclass(frozen=True)
class Principal:
    """A validated caller: the user's stable ID and the tenant from the token."""

    subject: str
    tenant_id: UUID


class TokenValidator(Protocol):
    async def validate(self, token: str) -> Principal: ...

    async def aclose(self) -> None: ...


def _principal(claims: dict[str, object], tenant_claim: str) -> Principal:
    oid = claims.get("oid")
    if not isinstance(oid, str) or not oid:
        raise AuthenticationError("token has no user")
    raw = claims.get(tenant_claim)
    try:
        tenant_id = UUID(str(raw)) if isinstance(raw, str) else None
    except ValueError:
        tenant_id = None
    if tenant_id is None:
        raise AuthenticationError("token has no tenant")
    return Principal(subject=oid, tenant_id=tenant_id)


class DevTokenValidator:
    """Local development and tests only."""

    def __init__(self, key: SecretStr, environment: Environment, tenant_claim: str) -> None:
        if not environment.is_local:
            raise ConfigurationError(
                f"dev tokens are local/test only, not ENVIRONMENT={environment}"
            )
        if len(key.get_secret_value()) < 32:
            raise ConfigurationError("PLATFORM_DEV_TOKEN_KEY must be at least 32 characters")
        self._key = key.get_secret_value()
        self._tenant_claim = tenant_claim

    def mint(self, *, subject: str, tenant_id: UUID, lifetime: int = DEV_LIFETIME) -> str:
        now = int(time.time())
        claims = {
            "iss": DEV_ISSUER,
            "aud": DEV_AUDIENCE,
            "oid": subject,
            self._tenant_claim: str(tenant_id),
            "iat": now,
            "exp": now + lifetime,
        }
        return jwt.encode(claims, self._key, algorithm="HS256")

    async def validate(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                audience=DEV_AUDIENCE,
                issuer=DEV_ISSUER,
                leeway=LEEWAY,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid token") from exc
        return _principal(claims, self._tenant_claim)

    async def aclose(self) -> None:
        return None


class EntraTokenValidator:
    """Validates Entra External ID v2 access tokens for this API (Learn: claims validation).

    Checks: RS256 signature by a key from the tenant's `jwks_uri`; `iss` equal to the metadata
    issuer; `aud` equal to the API's client ID; `tid` equal to our External ID tenant; `scp`
    containing the API scope; `oid` and the tenant claim present.
    """

    def __init__(
        self,
        *,
        tenant_id: UUID,
        authority: str,
        client_id: str,
        scope: str,
        tenant_claim: str,
        http: HttpClient,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tenant_id = str(tenant_id)
        self._metadata_url = (
            f"{authority.rstrip('/')}/{tenant_id}/v2.0/.well-known/openid-configuration"
        )
        self._client_id = client_id
        self._scope = scope
        self._tenant_claim = tenant_claim
        self._http = http
        self._clock = clock
        self._issuer: str | None = None
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def _refresh(self, *, force: bool) -> None:
        async with self._lock:
            now = self._clock()
            if self._fetched_at is not None:
                age = now - self._fetched_at
                if age < (REFETCH_MIN_INTERVAL if force else KEYS_TTL):
                    return
            metadata = (await self._http.request("GET", self._metadata_url)).json()
            if not isinstance(metadata, dict):
                raise AuthenticationError("identity provider metadata is invalid")
            metadata = cast(dict[str, object], metadata)
            issuer, jwks_uri = metadata.get("issuer"), metadata.get("jwks_uri")
            if not isinstance(issuer, str) or not isinstance(jwks_uri, str):
                raise AuthenticationError("identity provider metadata is invalid")
            jwks = (await self._http.request("GET", jwks_uri)).json()
            keys: dict[str, jwt.PyJWK] = {}
            for entry in jwt.PyJWKSet.from_dict(jwks).keys:
                if entry.key_id and entry.key_type == "RSA":
                    keys[entry.key_id] = entry
            self._issuer, self._keys, self._fetched_at = issuer, keys, now

    async def _key(self, kid: str) -> jwt.PyJWK:
        await self._refresh(force=False)
        if kid not in self._keys:
            await self._refresh(force=True)
        key = self._keys.get(kid)
        if key is None:
            raise AuthenticationError("token signed by an unknown key")
        return key

    async def validate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid token") from exc
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str):
            raise AuthenticationError("invalid token")
        try:
            key = await self._key(kid)
        except AuthenticationError:
            raise
        except VenueAgentsError as exc:
            logger.warning("identity provider keys unavailable (%s)", exc.code)
            raise AuthenticationError("cannot validate tokens right now") from exc
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._issuer,
                leeway=LEEWAY,
                options={"require": ["exp", "iss", "aud", "tid", "oid"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid token") from exc
        if claims.get("tid") != self._tenant_id:
            raise AuthenticationError("token from another directory")
        scopes = str(claims.get("scp", "")).split()
        if self._scope not in scopes:
            raise AuthenticationError("token lacks the API scope")
        return _principal(claims, self._tenant_claim)

    async def aclose(self) -> None:
        await self._http.aclose()


def build_validator(settings: PlatformApiSettings) -> TokenValidator:
    """Entra when configured; else the dev validator on a developer machine; else refuse."""
    entra = (settings.entra_tenant_id, settings.entra_authority, settings.entra_api_client_id)
    if all(v is not None for v in entra):
        return EntraTokenValidator(
            tenant_id=cast(UUID, settings.entra_tenant_id),
            authority=str(settings.entra_authority),
            client_id=cast(str, settings.entra_api_client_id),
            scope=settings.entra_api_scope,
            tenant_claim=settings.tenant_claim,
            http=HttpClient.from_settings("entra", settings),
        )
    if (
        settings.environment.is_local
        and not settings.hosted_in_azure
        and settings.platform_dev_token_key is not None
    ):
        return DevTokenValidator(
            settings.platform_dev_token_key, settings.environment, settings.tenant_claim
        )
    raise ConfigurationError(
        "no token validator: set ENTRA_TENANT_ID, ENTRA_AUTHORITY and ENTRA_API_CLIENT_ID, or "
        f"PLATFORM_DEV_TOKEN_KEY locally (ENVIRONMENT={settings.environment})"
    )
