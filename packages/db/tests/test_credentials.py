"""Password providers and the runtime engine's connect-time password seam."""

from __future__ import annotations

import pytest
from azure.core.credentials import AccessToken
from pydantic import SecretStr
from sqlalchemy import URL, text

from va_core.errors import ConfigurationError
from va_core.settings import Environment
from va_db.credentials import (
    POSTGRES_ENTRA_SCOPE,
    EntraTokenPassword,
    SecretPassword,
    check_deployed_tls,
)
from va_db.session import create_runtime_engine
from va_db.settings import MigrationSettings
from va_db.testing import temp_database


class FakeCredential:
    def __init__(self) -> None:
        self.scopes: list[tuple[str, ...]] = []

    async def get_token(self, *scopes: str, **_kwargs: object) -> AccessToken:
        self.scopes.append(scopes)
        return AccessToken("entra-token-value", 0)

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> FakeCredential:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeStore:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.reads = 0

    async def get(self, name: str) -> SecretStr:
        self.reads += 1
        return SecretStr(self.values[name])

    async def set(self, name: str, value: SecretStr) -> None:
        self.values[name] = value.get_secret_value()

    async def aclose(self) -> None:
        return None


async def test_entra_password_requests_the_postgres_scope() -> None:
    credential = FakeCredential()
    secret = await EntraTokenPassword(credential)()  # pyright: ignore[reportArgumentType]
    assert secret.get_secret_value() == "entra-token-value"
    assert credential.scopes == [(POSTGRES_ENTRA_SCOPE,)]
    assert "entra-token-value" not in repr(secret)


async def test_secret_password_rejects_bad_names() -> None:
    with pytest.raises(ValueError, match="invalid secret name"):
        SecretPassword(FakeStore({}), "not a name")


def test_password_in_url_and_provider_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="not both"):
        create_runtime_engine(
            "postgresql+psycopg://u:p@127.0.0.1/db",
            password=SecretPassword(FakeStore({}), "x"),
        )


@pytest.mark.db
async def test_engine_asks_the_provider_for_each_new_connection() -> None:
    admin_url = MigrationSettings.load().database_admin_url
    if admin_url is None:
        pytest.fail("DATABASE_ADMIN_URL must be set (see .env.example); db tests need Postgres")
    with temp_database(admin_url.get_secret_value()) as db:
        assert db.app_url.password is not None
        store = FakeStore({"db-password": str(db.app_url.password)})
        app = db.app_url
        url = URL.create(
            app.drivername,
            username=app.username,
            host=app.host,
            port=app.port,
            database=app.database,
        )
        engine = create_runtime_engine(
            url, password=SecretPassword(store, "db-password"), pool_size=1, max_overflow=0
        )
        try:
            async with engine.connect() as conn:
                user = (await conn.execute(text("SELECT current_user"))).scalar_one()
            assert user == db.app_url.username
            assert store.reads == 1
            await engine.dispose()  # drop the pooled connection: the next one logs in again
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            assert store.reads == 2
        finally:
            await engine.dispose()


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("postgresql+psycopg://u@db.example.com/d?sslmode=verify-full&sslrootcert=system", "host"),
        ("postgresql+psycopg://u@s.postgres.database.azure.com/d?sslmode=require", "verify-full"),
        ("postgresql+psycopg://u@s.postgres.database.azure.com/d?sslmode=verify-ca", "verify-full"),
        ("postgresql+psycopg://u@s.postgres.database.azure.com/d", "verify-full"),
        (
            "postgresql+psycopg://u@s.postgres.database.azure.com/d?sslmode=verify-full",
            "sslrootcert",
        ),
    ],
)
def test_deployed_tls_is_verified_azure_only(url: str, match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        check_deployed_tls(url, Environment.PROD)
    check_deployed_tls(url, Environment.LOCAL)  # anything goes on a developer machine


def test_deployed_tls_accepts_verified_azure_hosts() -> None:
    check_deployed_tls(
        "postgresql+psycopg://u@s.postgres.database.azure.com/d?sslmode=verify-full"
        "&sslrootcert=system",
        Environment.PROD,
    )


@pytest.mark.parametrize("key", ["password", "passfile"])
def test_password_in_the_query_is_refused(key: str) -> None:
    with pytest.raises(ConfigurationError, match="URL query"):
        create_runtime_engine(
            f"postgresql+psycopg://u@127.0.0.1/db?{key}=x",
            password=SecretPassword(FakeStore({}), "x"),
        )


def test_runtime_engine_hides_bound_parameters_in_errors() -> None:
    engine = create_runtime_engine("postgresql+psycopg://u:p@127.0.0.1/db")
    assert engine.sync_engine.hide_parameters
