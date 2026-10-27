"""SQLAlchemy models for the global catalog database (control plane; ADR-0008, ADR-0017).

The catalog maps every venue to its stamp, so it is cross-tenant by design and has no RLS. Only the
control plane reaches it, as a member of `va_catalog`. Tenant-owned rows still carry `tenant_id` in
their keys so they line up with the stamp schema and a later move-venue tool.

It holds no people or business data: those live in the stamp. The only names are an onboarding
job's inputs (organisation and venue names, timezone), kept so a failed job can be retried.
The migrations under `va_db/catalog/migrations` are the source of truth; these models must match
them (`packages/db/tests/test_catalog_migrations.py`).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from va_db.ids import uuid7
from va_db.models import NAMING_CONVENTION

STAMP_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
"""Stamp IDs appear in secret names and hostnames: lowercase letters, digits and single dashes."""
STAMP_ID = re.compile(STAMP_ID_PATTERN)


class CatalogBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def _check_in(column: str, values: type[StrEnum]) -> CheckConstraint:
    allowed = ", ".join(f"'{v.value}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=column)


class Ring(StrEnum):
    CANARY = "canary"
    GENERAL = "general"


class StampStatus(StrEnum):
    ACTIVE = "active"
    """Takes new tenants."""
    DRAINING = "draining"
    """Serves its tenants; takes new venues for them, but no new tenants."""
    CLOSED = "closed"
    """Takes nothing new."""


class DirectoryStatus(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDING = "offboarding"
    OFFBOARDED = "offboarded"


class SslMode(StrEnum):
    """libpq sslmode for a stamp connection. `disable` is refused outside local (provisioner)."""

    DISABLE = "disable"
    REQUIRE = "require"
    VERIFY_CA = "verify-ca"
    VERIFY_FULL = "verify-full"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Stamp(CatalogBase):
    """A deployment stamp. Registered by operators (admin CLI), never through the API.

    The connection columns are not secrets: the control plane authenticates with its managed
    identity in Azure, or a local password from the secrets interface.
    """

    __tablename__ = "stamp"
    __table_args__ = (
        CheckConstraint(f"id ~ '{STAMP_ID_PATTERN}'", name="id"),
        CheckConstraint("capacity > 0", name="capacity"),
        _check_in("ring", Ring),
        _check_in("status", StampStatus),
        _check_in("db_sslmode", SslMode),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    region: Mapped[str] = mapped_column(Text)
    ring: Mapped[str] = mapped_column(Text)
    capacity: Mapped[int]
    status: Mapped[str] = mapped_column(Text, server_default=StampStatus.ACTIVE.value)
    db_host: Mapped[str] = mapped_column(Text)
    db_port: Mapped[int] = mapped_column(server_default="5432")
    db_name: Mapped[str] = mapped_column(Text)
    db_provision_user: Mapped[str] = mapped_column(Text)
    """Login (a `va_provision` member) the control plane uses on this stamp's database."""
    db_sslmode: Mapped[str] = mapped_column(Text, server_default=SslMode.VERIFY_FULL.value)
    created_at: Mapped[datetime] = _created_at()


class TenantDirectory(CatalogBase):
    """Which stamp a tenant lives on. Every venue of a tenant is on that stamp."""

    __tablename__ = "tenant_directory"
    __table_args__ = (
        ForeignKeyConstraint(["stamp_id"], ["stamp.id"]),
        UniqueConstraint("tenant_id", "stamp_id"),
        Index(None, "stamp_id"),
        _check_in("status", DirectoryStatus),
    )

    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    stamp_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=DirectoryStatus.PROVISIONING.value)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class VenueDirectory(CatalogBase):
    """Venue -> stamp. `venue_id` is globally unique: it is what the edge routes on."""

    __tablename__ = "venue_directory"
    __table_args__ = (
        # Same stamp as the tenant: a venue can't be placed apart from its tenant.
        ForeignKeyConstraint(
            ["tenant_id", "stamp_id"],
            ["tenant_directory.tenant_id", "tenant_directory.stamp_id"],
            deferrable=True,
            initially="IMMEDIATE",
        ),
        UniqueConstraint("venue_id"),
        Index(None, "stamp_id", "status"),
        _check_in("status", DirectoryStatus),
    )

    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    venue_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    stamp_id: Mapped[str] = mapped_column(Text)
    region: Mapped[str] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=DirectoryStatus.PROVISIONING.value)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Entitlement(CatalogBase):
    """What a venue has bought, e.g. an agent (`events_manager`); flags sit on top (P0-12)."""

    __tablename__ = "entitlement"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "venue_id"], ["venue_directory.tenant_id", "venue_directory.venue_id"]
        ),
        CheckConstraint("entitlement ~ '^[a-z][a-z0-9_]{0,62}$'", name="entitlement"),
    )

    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    venue_id: Mapped[UUID] = mapped_column(primary_key=True)
    entitlement: Mapped[str] = mapped_column(Text, primary_key=True)
    granted_at: Mapped[datetime] = _created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set instead of deleting (the catalog role has no DELETE)."""


class OnboardingJob(CatalogBase):
    """Progress of onboarding one venue, with the inputs needed to (re)provision it in the stamp.

    `tenant_name` is set only when the job creates the tenant. `error_code` is a code, never free
    text or PII.
    """

    __tablename__ = "onboarding_job"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "venue_id"], ["venue_directory.tenant_id", "venue_directory.venue_id"]
        ),
        Index(None, "tenant_id", "venue_id"),
        _check_in("status", JobStatus),
        UniqueConstraint("idempotency_key"),
        CheckConstraint("idempotency_key ~ '^[A-Za-z0-9_-]{1,128}$'", name="idempotency_key"),
    )

    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    venue_id: Mapped[UUID]
    tenant_name: Mapped[str | None] = mapped_column(Text)
    venue_name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=JobStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    """The caller's Idempotency-Key: replaying POST /venues returns this job."""
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
