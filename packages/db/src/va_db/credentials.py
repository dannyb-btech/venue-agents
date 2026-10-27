"""Where database passwords come from: Entra tokens in Azure, the secrets interface locally.

Deployed services log in to Azure Database for PostgreSQL with their managed identity: the access
token is the password and the Postgres role is the identity's display name (mapped with
`pgaadauth_create_principal*` and made a member of a group role such as `va_app`). Tokens last
5-60 minutes and are checked only at login (open sessions are expected to survive expiry: standard
Postgres, not stated on Learn), so each new connection asks for one (azure-identity caches it
until near expiry).
Learn: azure/postgresql/security/security-connect-with-managed-identity.

Not verified until a real server exists (ADR-0017): group-role membership and NOBYPASSRLS on
Entra-mapped roles, and user-assigned identity OID mapping. Check them in P0-11.
"""

from __future__ import annotations

from azure.core.credentials_async import AsyncTokenCredential
from pydantic import SecretStr
from sqlalchemy import URL, make_url

from va_core.errors import ConfigurationError
from va_core.secrets import SecretStore, validate_secret_name
from va_core.settings import Environment

POSTGRES_ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
"""Azure public cloud (incl. Australia East). Sovereign clouds differ: `az cloud show`."""

ENTRA_POOL_RECYCLE_SECONDS = 30 * 60
"""Recycle pooled connections so logins are re-checked (a removed identity loses access)."""


AZURE_POSTGRES_HOST_SUFFIX = ".postgres.database.azure.com"


def check_deployed_tls(url: str | URL, environment: Environment) -> None:
    """Outside local/test: Azure Postgres only, `sslmode=verify-full`, an explicit `sslrootcert`.

    Entra tokens are sent as the password, so a downgraded or mis-addressed connection would hand
    a credential to someone else. `sslrootcert=system` (libpq 16+) or a pinned Azure CA bundle.
    """
    if environment.is_local:
        return
    parsed = make_url(url)
    host = parsed.host or ""
    if not host.endswith(AZURE_POSTGRES_HOST_SUFFIX):
        raise ConfigurationError(
            f"database host must end with {AZURE_POSTGRES_HOST_SUFFIX} outside local"
        )
    if parsed.query.get("sslmode") != "verify-full":
        raise ConfigurationError("database connections need sslmode=verify-full outside local")
    if not parsed.query.get("sslrootcert"):
        raise ConfigurationError("database connections need sslrootcert outside local")


class EntraTokenPassword:
    """A `PasswordProvider` returning an Entra access token for Azure Postgres."""

    def __init__(self, credential: AsyncTokenCredential) -> None:
        self._credential = credential

    async def __call__(self) -> SecretStr:
        token = await self._credential.get_token(POSTGRES_ENTRA_SCOPE)
        return SecretStr(token.token)


class SecretPassword:
    """A `PasswordProvider` reading a named secret (local `.env` or Key Vault). Local dev mostly."""

    def __init__(self, store: SecretStore, name: str) -> None:
        self._store = store
        self._name = validate_secret_name(name)

    async def __call__(self) -> SecretStr:
        return await self._store.get(self._name)
