"""Platform API errors. Codes are part of the API: the venue app branches on them."""

from __future__ import annotations

from va_core.errors import AuthorizationError, VenueAgentsError


class TenantNotOnStampError(AuthorizationError):
    """The token's tenant doesn't live on this stamp (ADR-0019)."""

    code = "tenant_not_on_stamp"


class TenantSuspendedError(AuthorizationError):
    """The tenant exists here but is suspended or offboarding."""

    code = "tenant_suspended"


class NotAUserError(AuthorizationError):
    """No active user for this token in the tenant."""

    code = "not_a_user"


class ConflictError(VenueAgentsError):
    """The request conflicts with the record's current state."""

    code = "conflict"


class DraftDecidedError(ConflictError):
    code = "draft_decided"


class DraftChangedError(ConflictError):
    """The draft's payload differs from what the approver was shown."""

    code = "draft_changed"


class UnprocessableError(VenueAgentsError):
    """The body is well-formed but can't be applied."""

    code = "unprocessable"


class EditNotSupportedError(UnprocessableError):
    code = "edit_not_supported"


class OAuthStateError(UnprocessableError):
    """The OAuth state is unknown, expired, used, or belongs to another user, venue or system."""

    code = "invalid_oauth_state"
