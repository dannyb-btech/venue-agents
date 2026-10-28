"""From a validated token to what the caller may do, read from the stamp database (ADR-0019).

1. The token's tenant must exist and be active on this stamp (else `tenant_not_on_stamp`).
2. The token's `oid` must be an active `app_user` of that tenant (else `not_a_user`).
3. Roles come from `role_assignment`; a NULL venue means every venue of the tenant.
4. Venue-scoped requests name a venue in `X-Venue-Id`; it must be active and the user must hold a
   role there. The resulting `TenantContext` is the only context venue code ever sees.

All of it runs under RLS for the token's tenant, so another tenant's rows are invisible anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from va_core.errors import AuthorizationError
from va_core.tenancy import Role, TenantContext, TenantIdentity
from va_db.models import (
    AppUser,
    RoleAssignment,
    Tenant,
    TenantStatus,
    UserStatus,
    Venue,
    VenueStatus,
)
from va_db.session import tenant_session
from va_platform_api.auth import Principal
from va_platform_api.errors import NotAUserError, TenantNotOnStampError


@dataclass(frozen=True)
class VenueAccess:
    venue_id: UUID
    name: str
    roles: frozenset[Role]


@dataclass(frozen=True)
class Member:
    """A resolved user: tenant identity, user row and the venues they can act on."""

    identity: TenantIdentity
    user_id: UUID
    email: str | None
    display_name: str | None
    venues: tuple[VenueAccess, ...]

    def context_for(self, venue_id: UUID) -> TenantContext:
        """The context for one venue, or 403 if the user holds no role there."""
        access = next((v for v in self.venues if v.venue_id == venue_id), None)
        if access is None:
            raise AuthorizationError("no access to this venue")
        return TenantContext(
            tenant_id=self.identity.tenant_id,
            venue_id=venue_id,
            stamp_id=self.identity.stamp_id,
            user_id=str(self.user_id),
            roles=access.roles,
        )


async def resolve_member(
    sessions: async_sessionmaker[AsyncSession], principal: Principal, stamp_id: str
) -> Member:
    identity = TenantIdentity(
        tenant_id=principal.tenant_id, stamp_id=stamp_id, user_id=principal.subject
    )
    async with tenant_session(sessions, identity) as session:
        tenant_status = await session.scalar(select(Tenant.status))
        if tenant_status != TenantStatus.ACTIVE:
            raise TenantNotOnStampError("this tenant is not served here")
        user = await session.scalar(
            select(AppUser).where(AppUser.external_subject == principal.subject)
        )
        if user is None or user.status != UserStatus.ACTIVE:
            raise NotAUserError("no active user for this sign-in")
        assignments = (
            await session.execute(
                select(RoleAssignment.venue_id, RoleAssignment.role).where(
                    RoleAssignment.user_id == user.id
                )
            )
        ).all()
        venues = (
            await session.execute(
                select(Venue.id, Venue.name)
                .where(Venue.status == VenueStatus.ACTIVE)
                .order_by(Venue.name)
            )
        ).all()
    tenant_wide = frozenset(Role(a.role) for a in assignments if a.venue_id is None)
    access: list[VenueAccess] = []
    for venue in venues:
        roles = tenant_wide | {Role(a.role) for a in assignments if a.venue_id == venue.id}
        if roles:
            access.append(VenueAccess(venue_id=venue.id, name=venue.name, roles=frozenset(roles)))
    return Member(
        identity=identity,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        venues=tuple(access),
    )
