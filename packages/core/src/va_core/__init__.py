"""Shared core: tenancy context, settings, telemetry, secrets, errors, HTTP client."""

from va_core.errors import (
    AuthorizationError,
    ConfigurationError,
    ExternalServiceError,
    NotFoundError,
    RateLimitedError,
    SecretNotFoundError,
    TenantContextError,
    UpstreamTimeoutError,
    VenueAgentsError,
)
from va_core.settings import CoreSettings, Environment, get_settings
from va_core.tenancy import (
    Role,
    TenantContext,
    TenantIdentity,
    current_run_id,
    current_tenant,
    current_tenant_or_none,
    run_scope,
    tenant_scope,
)

__all__ = [
    "AuthorizationError",
    "ConfigurationError",
    "CoreSettings",
    "Environment",
    "ExternalServiceError",
    "NotFoundError",
    "RateLimitedError",
    "Role",
    "SecretNotFoundError",
    "TenantContext",
    "TenantContextError",
    "TenantIdentity",
    "UpstreamTimeoutError",
    "VenueAgentsError",
    "current_run_id",
    "current_tenant",
    "current_tenant_or_none",
    "get_settings",
    "run_scope",
    "tenant_scope",
]
