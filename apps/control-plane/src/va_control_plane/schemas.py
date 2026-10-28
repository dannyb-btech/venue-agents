"""Request and response bodies for the control plane API."""

from __future__ import annotations

from typing import Annotated, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from va_core.tenancy import Role
from va_db.catalog.models import STAMP_ID_PATTERN, DirectoryStatus, JobStatus, Ring, StampStatus

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
EntitlementName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
StampId = Annotated[str, StringConstraints(pattern=STAMP_ID_PATTERN, max_length=63)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OnboardVenueIn(_Body):
    """Onboard a venue: for a new tenant (`tenant_name`) or an existing one (`tenant_id`).

    `tenant_id` here names the tenant being managed by an authorised operator; the control plane
    never uses it as the tenant context of a venue request (ADR-0017).
    """

    tenant_id: UUID | None = None
    tenant_name: Name | None = None
    venue_name: Name
    timezone: str = "Australia/Sydney"
    region: Annotated[str, StringConstraints(pattern=r"^[a-z0-9]{1,40}$")]
    plan: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,40}$")]
    ring: Ring = Ring.GENERAL
    stamp_id: StampId | None = None
    entitlements: Annotated[list[EntitlementName], Field(max_length=50)] = []

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        if value not in available_timezones():
            raise ValueError("unknown IANA timezone")
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def _one_tenant(self) -> Self:
        if (self.tenant_id is None) == (self.tenant_name is None):
            raise ValueError("give exactly one of tenant_id (existing) and tenant_name (new)")
        return self


class AddUserIn(_Body):
    """Add a venue user to the venue's stamp (ADR-0019). The person must also exist in Entra
    External ID with our tenant ID in their tenant attribute; this endpoint doesn't touch Entra."""

    external_subject: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{1,64}$")] = Field(
        description="The user's Entra object ID (`oid`)"
    )
    email: Annotated[str, StringConstraints(max_length=320)] | None = None
    display_name: Name | None = None
    role: Role
    tenant_wide: bool = False
    """Grant the role on every venue of the tenant, not just this one."""


class UserOut(_Body):
    user_id: UUID
    venue_id: UUID
    role: Role
    tenant_wide: bool


class OnboardingOut(_Body):
    tenant_id: UUID
    venue_id: UUID
    stamp_id: str
    job_id: UUID
    status: JobStatus
    error_code: str | None


class RouteOut(_Body):
    venue_id: UUID
    stamp_id: str
    region: str
    status: DirectoryStatus


class EntitlementsOut(_Body):
    venue_id: UUID
    entitlements: list[str]


class StampOut(_Body):
    id: str
    region: str
    ring: Ring
    status: StampStatus
    capacity: int
    venues: int


class ErrorOut(_Body):
    code: str
    message: str
