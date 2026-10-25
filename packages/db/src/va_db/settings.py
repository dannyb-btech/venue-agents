"""Settings for migrations and database test setup only.

`DATABASE_ADMIN_URL` is the schema owner's login: it bypasses RLS (locally it is a superuser). It
lives here, not in `va_core.CoreSettings`, so runtime services never load it.
"""

from __future__ import annotations

from typing import Self

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from va_core.settings import Environment


class MigrationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    environment: Environment
    database_admin_url: SecretStr | None = None

    @classmethod
    def load(cls) -> Self:
        # Pyright sees env-sourced required fields as missing constructor arguments.
        return cls()  # pyright: ignore[reportCallIssue]
