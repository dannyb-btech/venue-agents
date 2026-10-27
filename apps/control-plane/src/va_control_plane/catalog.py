"""Read queries on the catalog (routing, entitlements, stamp load).

The catalog is cross-tenant (ADR-0017): these functions look rows up by venue for an authorised
service caller and return catalog facts only; they never touch stamp data. (The one place catalog
state becomes a `TenantContext` is onboarding's provisioning step.)
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from va_control_plane.placement import StampLoad
from va_core.errors import NotFoundError
from va_db.catalog.models import (
    DirectoryStatus,
    Entitlement,
    Ring,
    Stamp,
    StampStatus,
    VenueDirectory,
)

ROUTABLE = (DirectoryStatus.ACTIVE, DirectoryStatus.SUSPENDED)
"""Suspended venues still route, so their stamp can tell the user why they can't act."""

# One placement at a time across the control plane, so two onboardings can't both take a stamp's
# last slot. Onboarding is rare; the lock is held for one short catalog transaction.
_PLACEMENT_LOCK = text("SELECT pg_advisory_xact_lock(hashtext('va_control_plane.placement'))")


@dataclass(frozen=True)
class Route:
    venue_id: UUID
    stamp_id: str
    region: str
    status: DirectoryStatus


async def route(session: AsyncSession, venue_id: UUID) -> Route:
    venue = (
        await session.execute(select(VenueDirectory).where(VenueDirectory.venue_id == venue_id))
    ).scalar_one_or_none()
    if venue is None or venue.status not in ROUTABLE:
        raise NotFoundError("venue not found")
    return Route(
        venue_id=venue.venue_id,
        stamp_id=venue.stamp_id,
        region=venue.region,
        status=DirectoryStatus(venue.status),
    )


async def entitlements(session: AsyncSession, venue_id: UUID) -> list[str]:
    venue = (
        await session.execute(select(VenueDirectory).where(VenueDirectory.venue_id == venue_id))
    ).scalar_one_or_none()
    if venue is None or venue.status == DirectoryStatus.OFFBOARDED:
        raise NotFoundError("venue not found")
    rows = await session.execute(
        select(Entitlement.entitlement)
        .where(
            Entitlement.tenant_id == venue.tenant_id,
            Entitlement.venue_id == venue_id,
            Entitlement.revoked_at.is_(None),
        )
        .order_by(Entitlement.entitlement)
    )
    return list(rows.scalars())


async def stamp_loads(session: AsyncSession) -> list[StampLoad]:
    placed = (
        select(VenueDirectory.stamp_id, func.count().label("venues"))
        .where(VenueDirectory.status != DirectoryStatus.OFFBOARDED)
        .group_by(VenueDirectory.stamp_id)
        .subquery()
    )
    rows = await session.execute(
        select(Stamp, func.coalesce(placed.c.venues, 0))
        .outerjoin(placed, placed.c.stamp_id == Stamp.id)
        .order_by(Stamp.id)
    )
    return [
        StampLoad(
            id=stamp.id,
            region=stamp.region,
            ring=Ring(stamp.ring),
            status=StampStatus(stamp.status),
            capacity=stamp.capacity,
            venues=int(venues),
        )
        for stamp, venues in rows
    ]


async def lock_placement(session: AsyncSession) -> None:
    await session.execute(_PLACEMENT_LOCK)
