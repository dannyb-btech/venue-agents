"""Who may call the control plane.

Callers are services and operators, not venue users: the edge (route lookups), stamp services
(entitlements) and operators (onboarding). Each request needs a bearer token granting a scope.
Until P0-05 brings Entra token validation, the only authenticator is a static dev token that is
refused outside ENVIRONMENT=local/test, so a deployed control plane can't start without real auth.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import SecretStr

from va_control_plane.settings import ControlPlaneSettings
from va_core.errors import AuthenticationError, ConfigurationError
from va_core.settings import Environment


class Scope(StrEnum):
    ROUTE_READ = "route:read"
    ENTITLEMENTS_READ = "entitlements:read"
    STAMPS_READ = "stamps:read"
    VENUES_WRITE = "venues:write"


@dataclass(frozen=True)
class Caller:
    subject: str
    scopes: frozenset[Scope]


class Authenticator(Protocol):
    async def authenticate(self, token: str) -> Caller: ...


class DevTokenAuthenticator:
    """Local development and tests only: one static token with every scope."""

    def __init__(self, token: SecretStr, environment: Environment) -> None:
        if not environment.is_local:
            raise ConfigurationError(
                f"the dev token authenticator is local/test only, not ENVIRONMENT={environment}"
            )
        if len(token.get_secret_value()) < 32:
            raise ConfigurationError("CONTROL_PLANE_DEV_TOKEN must be at least 32 characters")
        self._token = token.get_secret_value().encode()

    async def authenticate(self, token: str) -> Caller:
        if not hmac.compare_digest(token.encode(), self._token):
            raise AuthenticationError("invalid token")
        return Caller(subject="local-dev", scopes=frozenset(Scope))


def build_authenticator(settings: ControlPlaneSettings) -> Authenticator:
    if (
        settings.environment.is_local
        and not settings.hosted_in_azure
        and settings.control_plane_dev_token is not None
    ):
        return DevTokenAuthenticator(settings.control_plane_dev_token, settings.environment)
    raise ConfigurationError(
        f"no control plane authenticator for ENVIRONMENT={settings.environment}: set "
        "CONTROL_PLANE_DEV_TOKEN locally; Entra token validation arrives in P0-05"
    )
