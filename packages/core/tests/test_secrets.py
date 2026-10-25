from pathlib import Path
from uuid import uuid4

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from pydantic import SecretStr

from va_core import (
    ConfigurationError,
    CoreSettings,
    Environment,
    ExternalServiceError,
    SecretNotFoundError,
    TenantContext,
    TenantContextError,
)
from va_core.secrets import (
    EnvFileSecretStore,
    KeyVaultSecretStore,
    TenantSecrets,
    build_secret_store,
    tenant_secret_name,
    validate_secret_name,
)


def make_ctx() -> TenantContext:
    return TenantContext(tenant_id=uuid4(), venue_id=uuid4(), stamp_id="canary-local")


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text("XERO_CLIENT_SECRET=from-file\nEMPTY_ONE=\n", encoding="utf-8")
    return path


async def test_env_file_store_reads_file(env_file: Path) -> None:
    store = EnvFileSecretStore(env_file)
    assert (await store.get("xero-client-secret")).get_secret_value() == "from-file"
    with pytest.raises(SecretNotFoundError):
        await store.get("empty-one")
    with pytest.raises(SecretNotFoundError):
        await store.get("missing")
    await store.set("new-secret", SecretStr("v"))
    assert (await store.get("new-secret")).get_secret_value() == "v"


def test_secret_name_rules() -> None:
    assert validate_secret_name("t-abc-xero-refresh-token")
    for bad in ("", "1abc", "has_underscore", "has.dot", "a" * 128):
        with pytest.raises(ValueError):
            validate_secret_name(bad)


def test_tenant_secret_name() -> None:
    ctx = make_ctx()
    assert tenant_secret_name(ctx, "xero", "refresh-token") == (
        f"t-{ctx.tenant_id}-xero-refresh-token"
    )
    with pytest.raises(ValueError):
        tenant_secret_name(ctx, "Xero", "refresh_token")


async def test_tenant_secrets_refuse_other_tenants(env_file: Path) -> None:
    store = EnvFileSecretStore(env_file)
    mine, theirs = make_ctx(), make_ctx()
    secrets = TenantSecrets(store, mine)
    my_name = secrets.name_for("xero", "refresh-token")
    await secrets.set(my_name, SecretStr("mine"))
    assert (await secrets.get(my_name)).get_secret_value() == "mine"

    their_name = tenant_secret_name(theirs, "xero", "refresh-token")
    with pytest.raises(TenantContextError):
        await secrets.get(their_name)
    with pytest.raises(TenantContextError):
        await secrets.set(their_name, SecretStr("x"))
    with pytest.raises(TenantContextError):
        await secrets.get("xero-client-secret")  # platform secret, not tenant-scoped


class FakeSecret:
    def __init__(self, value: str | None) -> None:
        self.value = value


class FakeClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.gets = 0
        self.closed = False
        self.error: Exception | None = None

    async def get_secret(self, name: str) -> FakeSecret:
        self.gets += 1
        if self.error is not None:
            raise self.error
        if name not in self.values:
            raise ResourceNotFoundError("nope")
        return FakeSecret(self.values[name])

    async def set_secret(self, name: str, value: str) -> object:
        self.values[name] = value
        return FakeSecret(value)

    async def close(self) -> None:
        self.closed = True


class FakeCredential:
    closed = False

    async def close(self) -> None:
        self.closed = True


async def test_key_vault_store_caches_and_maps_errors() -> None:
    client, credential = FakeClient(), FakeCredential()
    now = [0.0]
    store = KeyVaultSecretStore(client, credential, cache_ttl=60, clock=lambda: now[0])
    client.values["api-key"] = "v1"

    assert (await store.get("api-key")).get_secret_value() == "v1"
    assert (await store.get("api-key")).get_secret_value() == "v1"
    assert client.gets == 1  # cached

    now[0] = 61
    client.values["api-key"] = "v2"
    assert (await store.get("api-key")).get_secret_value() == "v2"
    assert client.gets == 2  # expired

    await store.set("api-key", SecretStr("v3"))
    assert (await store.get("api-key")).get_secret_value() == "v3"
    assert client.gets == 2  # set refreshes the cache

    with pytest.raises(SecretNotFoundError):
        await store.get("missing")

    error = HttpResponseError("throttled")
    error.status_code = 429
    client.error = error
    with pytest.raises(ExternalServiceError) as info:
        await store.get("other")
    assert info.value.retryable

    await store.aclose()
    assert client.closed and credential.closed


async def test_key_vault_store_never_caches_rotating_tokens() -> None:
    client = FakeClient()
    store = KeyVaultSecretStore(client, clock=lambda: 0.0)
    name = f"t-{uuid4()}-xero-refresh-token"
    client.values[name] = "rt-1"
    assert (await store.get(name)).get_secret_value() == "rt-1"
    client.values[name] = "rt-2"  # rotated by another replica
    assert (await store.get(name)).get_secret_value() == "rt-2"
    await store.set(name, SecretStr("rt-3"))
    assert (await store.get(name)).get_secret_value() == "rt-3"
    assert client.gets == 3


def test_build_secret_store_selects_impl(env_file: Path) -> None:
    local = CoreSettings(environment=Environment.LOCAL)
    assert isinstance(build_secret_store(local, env_file), EnvFileSecretStore)

    deployed = CoreSettings.model_construct(environment=Environment.PROD, key_vault_url=None)
    with pytest.raises(ConfigurationError):
        build_secret_store(deployed, env_file)
