"""Platform API settings (on top of `CoreSettings`)."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, HttpUrl, SecretStr

from va_core.settings import CoreSettings


class PlatformApiSettings(CoreSettings):
    # Entra External ID (ADR-0019). The authority is the tenant's ciamlogin.com host, e.g.
    # https://<subdomain>.ciamlogin.com; tokens are v2 (`aud` = the API's client ID).
    entra_tenant_id: UUID | None = None
    entra_authority: HttpUrl | None = None
    entra_api_client_id: str | None = None
    entra_api_scope: str = Field(default="access_as_user", min_length=1)
    # The claim carrying our tenant ID (a custom user attribute or claims-provider claim).
    tenant_claim: str = Field(default="va_tenant_id", min_length=1)

    # Local/test only: HS256 key for dev tokens (32+ chars), standing in for Entra.
    platform_dev_token_key: SecretStr | None = None

    # Approval events. Locally a connection string (emulator); in Azure the namespace FQDN and
    # the managed identity.
    servicebus_connection_string: SecretStr | None = None
    servicebus_namespace: str | None = None
    approvals_topic: str = Field(default="approvals", min_length=1)

    # The venue app: OAuth providers redirect back to `<venue_app_url>/connections/callback`.
    venue_app_url: HttpUrl = HttpUrl("http://localhost:3000")
