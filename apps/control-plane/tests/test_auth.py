"""Caller authentication: the dev token is local-only; scopes are enforced per endpoint."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from va_control_plane.__main__ import main
from va_control_plane.api import app_from_settings, create_app
from va_control_plane.auth import (
    Authenticator,
    Caller,
    DevTokenAuthenticator,
    Scope,
    build_authenticator,
)
from va_control_plane.provisioning import ProvisionRequest, StampConnection, UserGrant
from va_control_plane.settings import ControlPlaneSettings
from va_core.errors import AuthenticationError, ConfigurationError
from va_core.settings import Environment

TOKEN = "t" * 40
VENUE = "0193a000-0000-7000-8000-0000000000a1"
ENDPOINTS = [
    ("GET", f"/route?venue_id={VENUE}"),
    ("GET", f"/venues/{VENUE}/entitlements"),
    ("GET", "/stamps"),
    ("POST", "/venues"),
    ("POST", f"/venues/{VENUE}/onboarding/retry"),
    ("POST", f"/venues/{VENUE}/users"),
]


class NoProvisioner:
    async def provision(self, stamp: StampConnection, request: ProvisionRequest) -> None:
        raise AssertionError("not reached")

    async def add_user(self, stamp: StampConnection, grant: UserGrant) -> UUID:
        raise AssertionError("not reached")

    async def aclose(self) -> None:
        return None


class RouteOnly:
    async def authenticate(self, token: str) -> Caller:
        return Caller(subject="edge", scopes=frozenset({Scope.ROUTE_READ}))


@pytest.fixture
async def unreachable() -> AsyncGenerator[AsyncEngine]:
    # Never connects: every request in this file is refused before the catalog is queried.
    engine = create_async_engine("postgresql+psycopg://nobody@127.0.0.1:1/none")
    yield engine
    await engine.dispose()


def _client(engine: AsyncEngine, authenticator: Authenticator) -> httpx.AsyncClient:
    app = create_app(
        catalog_engine=engine,
        provisioner=NoProvisioner(),
        authenticator=authenticator,
        check_roles=False,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cp")


async def test_dev_token_accepts_only_the_exact_token() -> None:
    auth = DevTokenAuthenticator(SecretStr(TOKEN), Environment.LOCAL)
    caller = await auth.authenticate(TOKEN)
    assert caller.scopes == frozenset(Scope)
    with pytest.raises(AuthenticationError):
        await auth.authenticate(TOKEN + "x")


@pytest.mark.parametrize("environment", [Environment.DEV, Environment.PROD])
def test_dev_token_is_refused_outside_local(environment: Environment) -> None:
    with pytest.raises(ConfigurationError, match="local/test only"):
        DevTokenAuthenticator(SecretStr(TOKEN), environment)


def test_dev_token_must_be_long() -> None:
    with pytest.raises(ConfigurationError, match="32 characters"):
        DevTokenAuthenticator(SecretStr("short"), Environment.LOCAL)


def test_no_authenticator_outside_local_means_no_start() -> None:
    settings = ControlPlaneSettings(
        environment=Environment.PROD,
        key_vault_url="https://kv.vault.azure.net",  # pyright: ignore[reportArgumentType]
        managed_identity_client_id="00000000-0000-0000-0000-000000000000",
        control_plane_dev_token=SecretStr(TOKEN),
    )
    with pytest.raises(ConfigurationError, match="P0-11/P0-12"):
        build_authenticator(settings)


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
async def test_every_endpoint_needs_a_valid_token(
    unreachable: AsyncEngine, method: str, path: str
) -> None:
    auth = DevTokenAuthenticator(SecretStr(TOKEN), Environment.TEST)
    async with _client(unreachable, auth) as client:
        missing = await client.request(method, path)
        wrong = await client.request(method, path, headers={"Authorization": "Bearer nope"})
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401
    assert "nope" not in wrong.text


@pytest.mark.parametrize(("method", "path"), ENDPOINTS[1:])
async def test_scopes_are_enforced(unreachable: AsyncEngine, method: str, path: str) -> None:
    async with _client(unreachable, RouteOnly()) as client:
        response = await client.request(method, path, headers={"Authorization": "Bearer edge"})
    assert response.status_code == 403


def test_app_refuses_to_start_in_prod_with_only_a_dev_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # keep the developer's .env out
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("KEY_VAULT_URL", "https://kv.vault.azure.net")
    monkeypatch.setenv("MANAGED_IDENTITY_CLIENT_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("CONTROL_PLANE_DEV_TOKEN", TOKEN)
    with pytest.raises(ConfigurationError, match="no control plane authenticator"):
        app_from_settings()


def test_serve_refuses_a_public_bind_with_the_dev_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("CONTROL_PLANE_DEV_TOKEN", TOKEN)
    with pytest.raises(ConfigurationError, match="loopback only"):
        main(["serve", "--host", "0.0.0.0"])  # noqa: S104 (the bind under test)
