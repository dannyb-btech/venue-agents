"""Typed settings. The only place the process environment is read.

Locally values come from `.env` (gitignored); in Azure, Container Apps injects them from
Key Vault references. Values that are secrets are `SecretStr` so they never appear in reprs or logs.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    DEV = "dev"
    PROD = "prod"

    @property
    def is_local(self) -> bool:
        """Developer machine or CI: no Azure resources required."""
        return self in (Environment.LOCAL, Environment.TEST)


class CoreSettings(BaseSettings):
    """Settings shared by every app. Apps subclass this for their own fields."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # `KEY_VAULT_URL=` in .env means unset, not an invalid empty URL.
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    # Required, no default: a deployed container missing ENVIRONMENT must fail, not act as local.
    environment: Environment
    stamp_id: str = Field(default="canary-local", min_length=1)
    service_name: str = "venue-agents"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Runtime connections log in as a member of `va_app` (no BYPASSRLS). The schema owner's URL
    # (DATABASE_ADMIN_URL) is deliberately not here: only `va_db.settings.MigrationSettings` has it.
    database_url: SecretStr | None = None
    database_connect_timeout: int = Field(default=10, gt=0)

    model_gateway_url: HttpUrl | None = None
    model_gateway_key: SecretStr | None = None
    model_triage: str = "claude-haiku"
    model_draft: str = "claude-sonnet"
    model_reasoning: str = "claude-opus"

    key_vault_url: HttpUrl | None = None
    managed_identity_client_id: str | None = None
    applicationinsights_connection_string: SecretStr | None = None

    http_connect_timeout: float = Field(default=5.0, gt=0)
    http_read_timeout: float = Field(default=30.0, gt=0)

    # Set by Azure hosting (Container Apps, App Service, managed identity endpoint). Used only to
    # refuse ENVIRONMENT=local/test on Azure, which would switch off every deployed-only guard.
    container_app_name: str | None = None
    website_site_name: str | None = None
    identity_endpoint: str | None = None

    @classmethod
    def load(cls) -> Self:
        """Load from the environment and `.env`. Fails if ENVIRONMENT is not set."""
        # Pyright sees env-sourced required fields as missing constructor arguments.
        return cls()  # pyright: ignore[reportCallIssue]

    @property
    def hosted_in_azure(self) -> bool:
        return any((self.container_app_name, self.website_site_name, self.identity_endpoint))

    @model_validator(mode="after")
    def _require_azure_settings_when_deployed(self) -> Self:
        if self.environment.is_local:
            if self.hosted_in_azure:
                raise ValueError(
                    f"ENVIRONMENT={self.environment} is for developer machines and CI, "
                    "but Azure hosting variables are set"
                )
            return self
        missing = [
            name
            for name in ("key_vault_url", "managed_identity_client_id")
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError(
                f"{', '.join(missing)} must be set when ENVIRONMENT={self.environment}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> CoreSettings:
    """Process-wide settings, loaded once."""
    return CoreSettings.load()
