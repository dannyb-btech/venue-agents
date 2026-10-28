"""Token validation: Entra External ID access tokens (ADR-0019) and the local dev issuer.

The Entra validator is exercised with a locally generated RSA key served from a fake metadata and
JWKS endpoint (httpx MockTransport), so every claim check is tested without a real tenant.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from va_core.errors import AuthenticationError, ConfigurationError
from va_core.http import HttpClient, RetryPolicy
from va_core.settings import Environment
from va_platform_api.auth import DevTokenValidator, EntraTokenValidator, build_validator
from va_platform_api.settings import PlatformApiSettings

DIRECTORY = UUID("11111111-2222-4333-8444-555555555555")
AUTHORITY = "https://venueagents.ciamlogin.com"
ISSUER = f"https://{DIRECTORY}.ciamlogin.com/{DIRECTORY}/v2.0"
JWKS_URI = f"https://venueagents.ciamlogin.com/{DIRECTORY}/discovery/v2.0/keys"
CLIENT_ID = "9b8f0c55-0000-4000-8000-00000000c1d0"
TENANT = uuid4()


def _rsa() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING = _rsa()
OTHER = _rsa()


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, object]:
    data = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    return {**data, "kid": kid, "use": "sig", "alg": "RS256"}


class Idp:
    """Fake metadata + JWKS endpoints; counts fetches."""

    def __init__(self) -> None:
        self.keys = [_jwk(SIGNING, "k1")]
        self.fetches = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            assert url == f"{AUTHORITY}/{DIRECTORY}/v2.0/.well-known/openid-configuration"
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS_URI})
        if url == JWKS_URI:
            self.fetches += 1
            return httpx.Response(200, json={"keys": self.keys})
        return httpx.Response(404)


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "tid": str(DIRECTORY),
        "oid": "user-oid-1",
        "scp": "openid access_as_user",
        "va_tenant_id": str(TENANT),
        "iat": now,
        "nbf": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(claims: dict[str, object], key: rsa.RSAPrivateKey = SIGNING, kid: str = "k1") -> str:
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def idp() -> Idp:
    return Idp()


@pytest.fixture
def make(idp: Idp) -> Callable[..., EntraTokenValidator]:
    def build(clock: Callable[[], float] = time.monotonic) -> EntraTokenValidator:
        http = HttpClient(
            "entra",
            transport=httpx.MockTransport(idp.handler),
            retry=RetryPolicy(max_attempts=1),
        )
        return EntraTokenValidator(
            tenant_id=DIRECTORY,
            authority=AUTHORITY,
            client_id=CLIENT_ID,
            scope="access_as_user",
            tenant_claim="va_tenant_id",
            http=http,
            clock=clock,
        )

    return build


async def test_valid_token(make: Callable[..., EntraTokenValidator]) -> None:
    principal = await make().validate(_sign(_claims()))
    assert principal.subject == "user-oid-1"
    assert principal.tenant_id == TENANT


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "another-api"},
        {"iss": "https://login.microsoftonline.com/x/v2.0"},
        {"tid": str(uuid4())},
        {"scp": "openid"},
        {"scp": None},
        {"oid": None},
        {"va_tenant_id": None},
        {"va_tenant_id": "not-a-uuid"},
        {"exp": int(time.time()) - 3600},
        {"nbf": int(time.time()) + 3600},
    ],
    ids=lambda o: next(iter(o)),
)
async def test_claim_checks(
    make: Callable[..., EntraTokenValidator], overrides: dict[str, object]
) -> None:
    with pytest.raises(AuthenticationError):
        await make().validate(_sign(_claims(**overrides)))


async def test_signature_from_another_key_is_refused(
    make: Callable[..., EntraTokenValidator],
) -> None:
    with pytest.raises(AuthenticationError):
        await make().validate(_sign(_claims(), key=OTHER, kid="k1"))


async def test_alg_none_and_hs256_confusion_are_refused(
    make: Callable[..., EntraTokenValidator],
) -> None:
    validator = make()
    body = _b64(json.dumps(_claims()).encode())
    unsigned = f"{_b64(json.dumps({'alg': 'none', 'kid': 'k1'}).encode())}.{body}."
    # HS256 keyed with the public key's PEM: the classic algorithm-confusion attack. Built by
    # hand because PyJWT refuses to mint it.
    pem = SIGNING.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode())
    mac = _b64(hmac.new(pem, f"{header}.{body}".encode(), hashlib.sha256).digest())
    confused = f"{header}.{body}.{mac}"
    for token in (unsigned, confused):
        with pytest.raises(AuthenticationError):
            await validator.validate(token)


async def test_unknown_kid_refetches_once_then_is_rate_limited(
    make: Callable[..., EntraTokenValidator], idp: Idp
) -> None:
    now = [1000.0]
    validator = make(clock=lambda: now[0])
    await validator.validate(_sign(_claims()))
    assert idp.fetches == 1

    # A rotated-in key is picked up by one refetch.
    idp.keys.append(_jwk(OTHER, "k2"))
    now[0] += 120
    await validator.validate(_sign(_claims(), key=OTHER, kid="k2"))
    assert idp.fetches == 2

    # Unknown kids can't make us hammer the endpoint.
    for _ in range(3):
        with pytest.raises(AuthenticationError, match="unknown key"):
            await validator.validate(_sign(_claims(), key=OTHER, kid="k-missing"))
    assert idp.fetches == 2


async def test_metadata_failure_is_401_not_500(idp: Idp) -> None:
    def broken(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    validator = EntraTokenValidator(
        tenant_id=DIRECTORY,
        authority=AUTHORITY,
        client_id=CLIENT_ID,
        scope="access_as_user",
        tenant_claim="va_tenant_id",
        http=HttpClient(
            "entra",
            transport=httpx.MockTransport(broken),
            retry=RetryPolicy(max_attempts=1),
        ),
    )
    with pytest.raises(AuthenticationError, match="right now"):
        await validator.validate(_sign(_claims()))


# --- dev tokens and wiring -----------------------------------------------------------------------


def test_dev_validator_is_local_only() -> None:
    with pytest.raises(ConfigurationError, match="local/test only"):
        DevTokenValidator(SecretStr("k" * 40), Environment.PROD, "va_tenant_id")
    with pytest.raises(ConfigurationError, match="32 characters"):
        DevTokenValidator(SecretStr("short"), Environment.LOCAL, "va_tenant_id")


async def test_dev_tokens_round_trip_and_expire() -> None:
    validator = DevTokenValidator(SecretStr("k" * 40), Environment.TEST, "va_tenant_id")
    principal = await validator.validate(validator.mint(subject="oid-1", tenant_id=TENANT))
    assert (principal.subject, principal.tenant_id) == ("oid-1", TENANT)
    with pytest.raises(AuthenticationError):
        await validator.validate(validator.mint(subject="oid-1", tenant_id=TENANT, lifetime=-3600))


def test_build_validator_prefers_entra_and_refuses_dev_tokens_on_azure() -> None:
    entra = PlatformApiSettings(
        environment=Environment.TEST,
        entra_tenant_id=DIRECTORY,
        entra_authority=AUTHORITY,  # pyright: ignore[reportArgumentType]
        entra_api_client_id=CLIENT_ID,
        platform_dev_token_key=SecretStr("k" * 40),
    )
    assert isinstance(build_validator(entra), EntraTokenValidator)
    dev = PlatformApiSettings(
        environment=Environment.TEST, platform_dev_token_key=SecretStr("k" * 40)
    )
    assert isinstance(build_validator(dev), DevTokenValidator)
    with pytest.raises(ConfigurationError, match="no token validator"):
        build_validator(PlatformApiSettings(environment=Environment.TEST))
    deployed = PlatformApiSettings(
        environment=Environment.DEV,
        key_vault_url="https://kv.vault.azure.net",  # pyright: ignore[reportArgumentType]
        managed_identity_client_id="mi",
        platform_dev_token_key=SecretStr("k" * 40),
    )
    with pytest.raises(ConfigurationError, match="no token validator"):
        build_validator(deployed)
