"""Signed run envelopes: how the tenant crosses from the agent runtime to a connector (ADR-0018).

The agent runtime mints a short-lived HS256 JWT per run step with a per-stamp key, addressed to one
connector system (`aud=va-connector:<system>`). It carries the tenant, venue, stamp, run, agent and
the agent's tool allow-list for that system. Connectors verify it on every MCP request and build the
`TenantContext` from it; tool arguments and model output never name a tenant.

Keys come from the secrets interface (`run-envelope-key-<kid>`). The first kid signs; every listed
kid verifies, so a key rotates by adding a new kid first and dropping the old one later.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, cast
from uuid import UUID

import jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from va_core.errors import ConfigurationError, EnvelopeError
from va_core.secrets import SecretStore, validate_secret_name
from va_core.tenancy import TenantContext

ENVELOPE_HEADER = "X-VA-Run-Envelope"
ISSUER = "va-agent-runtime"
ALGORITHM = "HS256"
MAX_LIFETIME = 600
"""Seconds. Longer envelopes are refused even when correctly signed."""
DEFAULT_TTL = 300
LEEWAY = 30
MIN_KEY_CHARS = 43
"""Keys are 32 random bytes, base64- or hex-encoded (43+ characters)."""

_KID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_SYSTEM = re.compile(r"^[a-z][a-z0-9]{0,31}$")
_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_REQUIRED = ("iss", "aud", "sub", "tid", "vid", "stamp", "run", "tools", "iat", "exp", "jti")


def audience(system: str) -> str:
    if not _SYSTEM.fullmatch(system):
        raise ValueError(f"invalid connector system {system!r}: lowercase letters and digits")
    return f"va-connector:{system}"


def envelope_key_secret_name(kid: str) -> str:
    if not _KID.fullmatch(kid):
        raise ValueError(f"invalid envelope key id {kid!r}")
    return validate_secret_name(f"run-envelope-key-{kid}")


class RunEnvelope(BaseModel):
    """What a verified envelope says. Immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID
    venue_id: UUID
    stamp_id: str = Field(min_length=1)
    run_id: UUID
    agent: str = Field(pattern=_AGENT.pattern)
    system: str = Field(pattern=_SYSTEM.pattern)
    tools: frozenset[str]
    issued_at: int
    expires_at: int
    jti: str

    def to_context(self) -> TenantContext:
        """The tenant context for this run. Agents hold no venue-user roles."""
        return TenantContext(
            tenant_id=self.tenant_id,
            venue_id=self.venue_id,
            stamp_id=self.stamp_id,
            user_id=f"agent:{self.agent}",
        )


class EnvelopeKeys:
    """Envelope keys by kid. The first kid signs; all verify."""

    def __init__(self, keys: Mapping[str, SecretStr], signing_kid: str) -> None:
        if signing_kid not in keys:
            raise ConfigurationError("the signing envelope key id has no key")
        for kid, key in keys.items():
            if not _KID.fullmatch(kid):
                raise ConfigurationError(f"invalid envelope key id {kid!r}")
            if len(key.get_secret_value()) < MIN_KEY_CHARS:
                raise ConfigurationError(
                    f"envelope key {kid!r} is shorter than {MIN_KEY_CHARS} characters "
                    "(use 32 random bytes, base64- or hex-encoded)"
                )
        self._keys = dict(keys)
        self.signing_kid = signing_kid

    @classmethod
    async def load(cls, store: SecretStore, kids: Sequence[str]) -> EnvelopeKeys:
        """Read `run-envelope-key-<kid>` for each kid; `kids[0]` signs."""
        if not kids:
            raise ConfigurationError("RUN_ENVELOPE_KIDS is empty")
        keys = {kid: await store.get(envelope_key_secret_name(kid)) for kid in kids}
        return cls(keys, kids[0])

    def key(self, kid: str) -> SecretStr | None:
        return self._keys.get(kid)


def issue_envelope(
    keys: EnvelopeKeys,
    ctx: TenantContext,
    *,
    system: str,
    run_id: UUID,
    agent: str,
    tools: Sequence[str],
    ttl: int = DEFAULT_TTL,
    clock: Callable[[], float] = time.time,
) -> str:
    """Mint an envelope for one connector system. Called by the agent runtime (and tests)."""
    aud = audience(system)
    if not _AGENT.fullmatch(agent):
        raise ValueError(f"invalid agent name {agent!r}")
    if not 0 < ttl <= MAX_LIFETIME:
        raise ValueError(f"ttl must be 1-{MAX_LIFETIME} seconds")
    foreign = [tool for tool in tools if not tool.startswith(f"{system}_")]
    if foreign:
        raise ValueError(f"tools {foreign!r} do not belong to connector {system!r}")
    now = int(clock())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": aud,
        "sub": agent,
        "tid": str(ctx.tenant_id),
        "vid": str(ctx.venue_id),
        "stamp": ctx.stamp_id,
        "run": str(run_id),
        "tools": sorted(set(tools)),
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(12),
    }
    key = keys.key(keys.signing_kid)
    if key is None:
        raise ConfigurationError("the signing envelope key id has no key")
    return jwt.encode(
        claims, key.get_secret_value(), algorithm=ALGORITHM, headers={"kid": keys.signing_kid}
    )


class EnvelopeVerifier(Protocol):
    def verify(self, token: str | None) -> RunEnvelope: ...


class HmacEnvelopeVerifier:
    """Verifies envelopes addressed to `system` on `stamp_id`. Raises `EnvelopeError`.

    Error messages say what was wrong, never the token or its claims.
    """

    def __init__(self, keys: EnvelopeKeys, *, system: str, stamp_id: str) -> None:
        self._keys = keys
        self._system = system
        self._audience = audience(system)
        self._stamp_id = stamp_id

    def verify(self, token: str | None) -> RunEnvelope:
        if not token:
            raise EnvelopeError("run envelope is missing")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise EnvelopeError("run envelope is malformed") from exc
        if header.get("alg") != ALGORITHM:
            raise EnvelopeError("run envelope uses a disallowed algorithm")
        kid = header.get("kid")
        key = self._keys.key(kid) if isinstance(kid, str) else None
        if key is None:
            raise EnvelopeError("run envelope is signed with an unknown key")
        try:
            claims = cast(
                dict[str, object],
                jwt.decode(
                    token,
                    key.get_secret_value(),
                    algorithms=[ALGORITHM],
                    audience=self._audience,
                    issuer=ISSUER,
                    leeway=LEEWAY,
                    options={"require": list(_REQUIRED), "strict_aud": True},
                ),
            )
        except jwt.ExpiredSignatureError as exc:
            raise EnvelopeError("run envelope has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise EnvelopeError(f"run envelope is invalid ({type(exc).__name__})") from exc
        if claims["stamp"] != self._stamp_id:
            raise EnvelopeError("run envelope is for another stamp")
        issued_at, expires_at = claims["iat"], claims["exp"]
        if not isinstance(issued_at, int) or not isinstance(expires_at, int):
            raise EnvelopeError("run envelope times are not integers")
        if expires_at - issued_at > MAX_LIFETIME:
            raise EnvelopeError("run envelope lifetime is too long")
        tools = claims["tools"]
        if not isinstance(tools, list) or not all(
            isinstance(tool, str) and tool.startswith(f"{self._system}_")
            for tool in cast(list[object], tools)
        ):
            raise EnvelopeError("run envelope tool list is invalid")
        try:
            return RunEnvelope(
                tenant_id=UUID(str(claims["tid"])),
                venue_id=UUID(str(claims["vid"])),
                stamp_id=str(claims["stamp"]),
                run_id=UUID(str(claims["run"])),
                agent=str(claims["sub"]),
                system=self._system,
                tools=frozenset(cast(list[str], tools)),
                issued_at=issued_at,
                expires_at=expires_at,
                jti=str(claims["jti"]),
            )
        except (ValueError, ValidationError) as exc:
            raise EnvelopeError("run envelope claims are invalid") from exc
