"""Typed errors shared by every app.

Raise these instead of bare exceptions so callers can branch on `code` and `retryable`.
Messages must never contain secret values, tokens or personal data.
"""

from __future__ import annotations


class VenueAgentsError(Exception):
    """Base class for all platform errors."""

    code: str = "error"
    retryable: bool = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationError(VenueAgentsError):
    """Settings are missing or inconsistent for the current environment."""

    code = "configuration"


class TenantContextError(VenueAgentsError):
    """No tenant is set, or code tried to switch tenant mid-flight."""

    code = "tenant_context"


class AuthorizationError(VenueAgentsError):
    """The caller lacks the role or scope for this operation."""

    code = "forbidden"


class NotFoundError(VenueAgentsError):
    """The requested record does not exist for this tenant."""

    code = "not_found"


class SecretNotFoundError(NotFoundError):
    """A secret reference does not resolve. The message names the secret, never its value."""

    code = "secret_not_found"


class ExternalServiceError(VenueAgentsError):
    """An external system returned an error or could not be reached.

    `retryable` means repeating the same request is safe. `outcome_unknown` means a request that
    is not safe to repeat may already have taken effect (timeout or 5xx after sending): the
    executor must reconcile with the external system, never blindly retry.
    """

    code = "external_service"

    def __init__(
        self,
        message: str,
        *,
        service: str,
        status: int | None = None,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.status = status
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown


class RateLimitedError(ExternalServiceError):
    """The external system asked us to back off for longer than we are willing to wait."""

    code = "rate_limited"

    def __init__(self, message: str, *, service: str, retry_after: float | None) -> None:
        super().__init__(message, service=service, status=429, retryable=True)
        self.retry_after = retry_after


class UpstreamTimeoutError(ExternalServiceError):
    """The external system did not respond in time after all retries."""

    code = "upstream_timeout"

    def __init__(
        self,
        message: str,
        *,
        service: str,
        retryable: bool = True,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(
            message,
            service=service,
            status=None,
            retryable=retryable,
            outcome_unknown=outcome_unknown,
        )
