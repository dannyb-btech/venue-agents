"""Secrets interface: Key Vault in Azure, the `.env` file locally.

Per-tenant credentials (OAuth refresh tokens, API keys) are named `t-<tenant_id>-<system>-<kind>`
and read through `TenantSecrets`, which refuses any name that belongs to another tenant.
Secret values are `SecretStr` and never logged; errors name the secret, never its value.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from dotenv import dotenv_values
from pydantic import SecretStr

from va_core.errors import (
    ConfigurationError,
    ExternalServiceError,
    SecretNotFoundError,
    TenantContextError,
)
from va_core.settings import CoreSettings
from va_core.tenancy import TenantContext

# Key Vault secret names: 1-127 chars, start with a letter, letters, digits and dashes only.
_SECRET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,126}$")
_NAME_PART = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_secret_name(name: str) -> str:
    if not _SECRET_NAME.fullmatch(name):
        raise ValueError(f"invalid secret name {name!r}: use letters, digits and dashes")
    return name


class SecretStore(Protocol):
    async def get(self, name: str) -> SecretStr: ...

    async def set(self, name: str, value: SecretStr) -> None: ...

    async def aclose(self) -> None: ...


class EnvFileSecretStore:
    """Local development only: reads secrets from a `.env` file (not the process environment).

    `xero-client-secret` resolves to the `XERO_CLIENT_SECRET` line. `set` keeps the value in memory.
    """

    def __init__(self, path: Path | str = ".env") -> None:
        self._values = {
            key: value for key, value in dotenv_values(path).items() if value is not None
        }
        self._overrides: dict[str, str] = {}

    @staticmethod
    def _key(name: str) -> str:
        return validate_secret_name(name).upper().replace("-", "_")

    async def get(self, name: str) -> SecretStr:
        key = self._key(name)
        value = self._overrides.get(key, self._values.get(key))
        if not value:
            raise SecretNotFoundError(f"secret {name!r} not found in env file")
        return SecretStr(value)

    async def set(self, name: str, value: SecretStr) -> None:
        self._overrides[self._key(name)] = value.get_secret_value()

    async def aclose(self) -> None:
        return None


class _KeyVaultSecret(Protocol):
    @property
    def value(self) -> str | None: ...


class _AsyncSecretClient(Protocol):
    def get_secret(self, name: str) -> Awaitable[_KeyVaultSecret]: ...

    def set_secret(self, name: str, value: str) -> Awaitable[object]: ...

    def close(self) -> Awaitable[None]: ...


class _AsyncClosable(Protocol):
    def close(self) -> Awaitable[None]: ...


class KeyVaultSecretStore:
    """Key Vault with an in-memory TTL cache (GETs are throttled; Learn recommends caching).

    Names ending in an `uncached_suffixes` entry (default `-token`: OAuth access/refresh tokens that
    rotate) always read through, so a replica never reuses a refresh token another one rotated.
    Serialising the refresh itself per tenant and system is the connector framework's job (P0-06).
    """

    def __init__(
        self,
        client: _AsyncSecretClient,
        credential: _AsyncClosable | None = None,
        *,
        cache_ttl: float = 300.0,
        uncached_suffixes: tuple[str, ...] = ("-token",),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._credential = credential
        self._cache_ttl = cache_ttl
        self._uncached_suffixes = uncached_suffixes
        self._clock = clock
        self._cache: dict[str, tuple[float, SecretStr]] = {}

    @classmethod
    def from_settings(cls, settings: CoreSettings) -> KeyVaultSecretStore:
        from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
        from azure.keyvault.secrets.aio import SecretClient

        if settings.key_vault_url is None:
            raise ConfigurationError("KEY_VAULT_URL is not set")
        credential: DefaultAzureCredential | ManagedIdentityCredential
        if settings.environment.is_local:
            credential = DefaultAzureCredential()
        else:
            # Deployed: a specific credential, not the probing default chain (Learn guidance).
            credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
        client = SecretClient(vault_url=str(settings.key_vault_url), credential=credential)
        return cls(client, credential)

    async def get(self, name: str) -> SecretStr:
        validate_secret_name(name)
        cacheable = not name.endswith(self._uncached_suffixes)
        now = self._clock()
        cached = self._cache.pop(name, None)
        if cacheable and cached is not None and now - cached[0] < self._cache_ttl:
            self._cache[name] = cached
            return cached[1]
        try:
            secret = await self._client.get_secret(name)
        except ResourceNotFoundError as exc:
            raise SecretNotFoundError(f"secret {name!r} not found in Key Vault") from exc
        except HttpResponseError as exc:
            status = exc.status_code
            raise ExternalServiceError(
                f"Key Vault error reading secret {name!r}",
                service="key_vault",
                status=status,
                retryable=status is not None and (status == 429 or status >= 500),
            ) from exc
        if secret.value is None:
            raise SecretNotFoundError(f"secret {name!r} has no value")
        value = SecretStr(secret.value)
        if cacheable:
            self._cache[name] = (now, value)
        return value

    async def set(self, name: str, value: SecretStr) -> None:
        validate_secret_name(name)
        try:
            await self._client.set_secret(name, value.get_secret_value())
        except HttpResponseError as exc:
            raise ExternalServiceError(
                f"Key Vault error writing secret {name!r}",
                service="key_vault",
                status=exc.status_code,
            ) from exc
        if not name.endswith(self._uncached_suffixes):
            self._cache[name] = (self._clock(), value)

    async def aclose(self) -> None:
        # The client does not close the credential it was given; close both.
        await self._client.close()
        if self._credential is not None:
            await self._credential.close()


def tenant_secret_name(ctx: TenantContext, system: str, kind: str) -> str:
    """Name of a per-tenant secret, e.g. `t-<tenant_id>-xero-refresh-token`."""
    for part in (system, kind):
        if not _NAME_PART.fullmatch(part):
            raise ValueError(
                f"invalid secret name part {part!r}: lowercase letters, digits, dashes"
            )
    return validate_secret_name(f"t-{ctx.tenant_id}-{system}-{kind}")


class TenantSecrets:
    """Per-tenant view of a secret store. Fails closed on another tenant's secret names."""

    def __init__(self, store: SecretStore, ctx: TenantContext) -> None:
        self._store = store
        self._ctx = ctx
        self._prefix = f"t-{ctx.tenant_id}-"

    def name_for(self, system: str, kind: str) -> str:
        return tenant_secret_name(self._ctx, system, kind)

    def _check(self, name: str) -> str:
        if not name.startswith(self._prefix):
            raise TenantContextError("secret reference does not belong to the current tenant")
        return name

    async def get(self, name: str) -> SecretStr:
        return await self._store.get(self._check(name))

    async def set(self, name: str, value: SecretStr) -> None:
        await self._store.set(self._check(name), value)


def build_secret_store(settings: CoreSettings, env_file: Path | str = ".env") -> SecretStore:
    """Key Vault when configured; the `.env` file only in local/test environments."""
    if settings.key_vault_url is not None:
        return KeyVaultSecretStore.from_settings(settings)
    if settings.environment.is_local:
        return EnvFileSecretStore(env_file)
    raise ConfigurationError(f"KEY_VAULT_URL is required when ENVIRONMENT={settings.environment}")
