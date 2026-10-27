"""Which stamp a new venue goes on (ADR-0017). Pure: the catalog supplies the stamps and their load.

1. A tenant's venues all live on the tenant's stamp (stamp-local data, roles and cross-venue views).
2. A new tenant goes on the operator's requested stamp if given (e.g. OR -> canary), else on the
   least-loaded active stamp of the requested ring in the region with spare capacity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from va_control_plane.errors import PlacementError
from va_db.catalog.models import Ring, StampStatus


@dataclass(frozen=True)
class StampLoad:
    id: str
    region: str
    ring: Ring
    status: StampStatus
    capacity: int
    venues: int
    """Venues placed on the stamp and not offboarded (provisioning ones count)."""

    @property
    def spare(self) -> int:
        return self.capacity - self.venues


def choose_stamp(
    stamps: Sequence[StampLoad],
    *,
    region: str,
    ring: Ring,
    tenant_stamp: str | None = None,
    requested: str | None = None,
) -> StampLoad:
    by_id = {stamp.id: stamp for stamp in stamps}
    if tenant_stamp is not None:
        if requested is not None and requested != tenant_stamp:
            raise PlacementError(
                "the tenant lives on another stamp; moving tenants is not supported"
            )
        stamp = by_id.get(tenant_stamp)
        if stamp is None:
            raise PlacementError(f"the tenant's stamp {tenant_stamp!r} is not registered")
        if stamp.region != region:
            raise PlacementError(f"the tenant's stamp is in {stamp.region}, not {region}")
        if stamp.status == StampStatus.CLOSED:
            raise PlacementError("the tenant's stamp is closed to new venues")
        if stamp.spare <= 0:
            raise PlacementError("the tenant's stamp is full")
        return stamp

    if requested is not None:
        stamp = by_id.get(requested)
        if stamp is None:
            raise PlacementError(f"stamp {requested!r} is not registered")
        if stamp.region != region:
            raise PlacementError(f"stamp {requested!r} is in {stamp.region}, not {region}")
        if stamp.status != StampStatus.ACTIVE:
            raise PlacementError(f"stamp {requested!r} is not taking new tenants")
        if stamp.spare <= 0:
            raise PlacementError(f"stamp {requested!r} is full")
        return stamp

    candidates = [
        stamp
        for stamp in stamps
        if stamp.status == StampStatus.ACTIVE
        and stamp.region == region
        and stamp.ring == ring
        and stamp.spare > 0
    ]
    if not candidates:
        raise PlacementError(f"no active {ring} stamp in {region} has capacity")
    return min(candidates, key=lambda stamp: (stamp.venues / stamp.capacity, stamp.id))
