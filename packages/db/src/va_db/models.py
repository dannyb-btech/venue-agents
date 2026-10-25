"""SQLAlchemy models for the stamp database (docs/architecture/data-model.md).

Conventions (database.md, ADR-0007/0008):
- Every table has `tenant_id`, defaulting to the session's tenant (`va_current_tenant()`), in its
  primary key `(tenant_id, id)` and in every foreign key, so a row can only reference rows of its
  own tenant and the schema can later be sharded by tenant.
- RLS policies and grants live in the migrations (Alembic can't see them); the isolation tests check
  every table has them.
- IDs are UUIDv7, timestamps `timestamptz` UTC, money `numeric` with a currency column.
- Status-like columns are text with CHECK constraints, mirrored by the StrEnums here.

The migrations are the source of truth for the schema; these models must match them
(`packages/db/tests/test_migrations.py` compares the two).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from va_core.tenancy import Role
from va_db.ids import uuid7

# Plain alias, not a PEP 695 `type` statement, so SQLAlchemy resolves it inside Mapped[...].
JsonObject = dict[str, object]

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _tenant_id(*, primary_key: bool = True) -> Mapped[UUID]:
    return mapped_column(
        primary_key=primary_key, server_default=text("va_current_tenant()"), sort_order=-2
    )


def _id() -> Mapped[UUID]:
    return mapped_column(primary_key=True, default=uuid7, sort_order=-1)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def _check_in(column: str, values: type[StrEnum]) -> CheckConstraint:
    allowed = ", ".join(f"'{v.value}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=column)


def _fk(
    columns: tuple[str, str], table: str, *, ondelete: str | None = None
) -> ForeignKeyConstraint:
    """Composite FK `(tenant_id, <col>) -> <table>(tenant_id, id)`."""
    return ForeignKeyConstraint(
        list(columns), [f"{table}.tenant_id", f"{table}.id"], ondelete=ondelete
    )


# --- Status vocabularies -------------------------------------------------------------------------


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDING = "offboarding"


class VenueStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"
    REVOKED = "revoked"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolCategory(StrEnum):
    """Agent tools may only read or draft (ADR-0005). There is deliberately no `write`."""

    READ = "read"
    DRAFT = "draft"


class ToolCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DraftStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXECUTED = "executed"
    FAILED = "failed"


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ActionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUSED = "refused"
    OUTCOME_UNKNOWN = "outcome_unknown"


# --- Platform ------------------------------------------------------------------------------------


class Tenant(Base):
    """The customer organisation. Its key is `tenant_id` alone."""

    __tablename__ = "tenant"
    __table_args__ = (_check_in("status", TenantStatus),)

    tenant_id: Mapped[UUID] = _tenant_id()
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=TenantStatus.ACTIVE.value)
    created_at: Mapped[datetime] = _created_at()


class Venue(Base):
    __tablename__ = "venue"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenant.tenant_id"]),
        _check_in("status", VenueStatus),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, server_default="Australia/Sydney")
    status: Mapped[str] = mapped_column(Text, server_default=VenueStatus.ACTIVE.value)
    created_at: Mapped[datetime] = _created_at()


class AppUser(Base):
    """A person who signs in for this tenant; `external_subject` is the Entra subject claim."""

    __tablename__ = "app_user"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenant.tenant_id"]),
        UniqueConstraint("tenant_id", "external_subject"),
        _check_in("status", UserStatus),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    external_subject: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=UserStatus.ACTIVE.value)
    created_at: Mapped[datetime] = _created_at()


class RoleAssignment(Base):
    """A role for a user, on one venue or (venue_id NULL) on every venue of the tenant."""

    __tablename__ = "role_assignment"
    __table_args__ = (
        _fk(("tenant_id", "user_id"), "app_user", ondelete="CASCADE"),
        _fk(("tenant_id", "venue_id"), "venue", ondelete="CASCADE"),
        UniqueConstraint(
            "tenant_id", "user_id", "venue_id", "role", postgresql_nulls_not_distinct=True
        ),
        _check_in("role", Role),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    user_id: Mapped[UUID]
    venue_id: Mapped[UUID | None]
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Connection(Base):
    """A venue's link to an external system. `secret_ref` names a Key Vault secret, not a value."""

    __tablename__ = "connection"
    __table_args__ = (
        _fk(("tenant_id", "venue_id"), "venue"),
        UniqueConstraint("tenant_id", "venue_id", "system"),
        _check_in("status", ConnectionStatus),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    venue_id: Mapped[UUID]
    system: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=ConnectionStatus.PENDING.value)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    secret_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class AgentConfig(Base):
    """Per-venue agent settings: targets (labour %, wine GP), approval rules, schedules."""

    __tablename__ = "agent_config"
    __table_args__ = (
        _fk(("tenant_id", "venue_id"), "venue"),
        UniqueConstraint("tenant_id", "venue_id", "agent"),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    venue_id: Mapped[UUID]
    agent: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(server_default="1")
    config: Mapped[JsonObject] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# --- Agent activity ------------------------------------------------------------------------------


class AgentRun(Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        _fk(("tenant_id", "venue_id"), "venue"),
        Index(None, "tenant_id", "venue_id", "started_at"),
        _check_in("status", RunStatus),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    venue_id: Mapped[UUID]
    agent: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=RunStatus.PENDING.value)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolCall(Base):
    """One tool invocation in a run. `args_summary` is redacted before it is stored. Append-only."""

    __tablename__ = "tool_call"
    __table_args__ = (
        _fk(("tenant_id", "run_id"), "agent_run"),
        Index(None, "tenant_id", "run_id"),
        _check_in("category", ToolCategory),
        _check_in("status", ToolCallStatus),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    run_id: Mapped[UUID]
    tool: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    args_summary: Mapped[JsonObject] = mapped_column(JSONB, server_default="{}")
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime] = _created_at()


class Draft(Base):
    """Something an agent proposes. Only the action executor acts on it, after an approval."""

    __tablename__ = "draft"
    __table_args__ = (
        _fk(("tenant_id", "venue_id"), "venue"),
        _fk(("tenant_id", "run_id"), "agent_run"),
        Index(None, "tenant_id", "venue_id", "status"),
        _check_in("status", DraftStatus),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    venue_id: Mapped[UUID]
    run_id: Mapped[UUID | None]
    type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=DraftStatus.PENDING.value)
    payload: Mapped[JsonObject] = mapped_column(JSONB)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list[JsonObject]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Approval(Base):
    """A human decision on a draft: one per draft, append-only (ADR-0006)."""

    __tablename__ = "approval"
    __table_args__ = (
        _fk(("tenant_id", "draft_id"), "draft"),
        _fk(("tenant_id", "user_id"), "app_user"),
        UniqueConstraint("tenant_id", "draft_id"),
        _check_in("decision", Decision),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    draft_id: Mapped[UUID]
    user_id: Mapped[UUID]
    decision: Mapped[str] = mapped_column(Text)
    edits: Mapped[JsonObject | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActionLog(Base):
    """What the executor did for an approval: one row per approval (idempotency key).

    A refusal (e.g. no approval exists) is logged with `approval_id` NULL.
    """

    __tablename__ = "action_log"
    __table_args__ = (
        _fk(("tenant_id", "approval_id"), "approval"),
        _fk(("tenant_id", "draft_id"), "draft"),
        UniqueConstraint("tenant_id", "approval_id"),
        CheckConstraint(
            f"approval_id IS NOT NULL OR status = '{ActionStatus.REFUSED.value}'",
            name="approval_required",
        ),
        _check_in("status", ActionStatus),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    approval_id: Mapped[UUID | None]
    draft_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(Text, server_default=ActionStatus.PENDING.value)
    external_id: Mapped[str | None] = mapped_column(Text)
    result: Mapped[JsonObject] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class UsageCost(Base):
    """Model usage per call. Cost keeps 6 decimal places (sub-cent calls); round when reporting."""

    __tablename__ = "usage_cost"
    __table_args__ = (
        _fk(("tenant_id", "venue_id"), "venue"),
        _fk(("tenant_id", "run_id"), "agent_run"),
        Index(None, "tenant_id", "venue_id", "created_at"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cached_input_tokens >= 0",
            name="tokens_non_negative",
        ),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    venue_id: Mapped[UUID]
    run_id: Mapped[UUID | None]
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(server_default="0")
    output_tokens: Mapped[int] = mapped_column(server_default="0")
    cached_input_tokens: Mapped[int] = mapped_column(server_default="0")
    cost: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    currency: Mapped[str] = mapped_column(String(3), server_default="AUD")
    created_at: Mapped[datetime] = _created_at()


# --- Sync state ----------------------------------------------------------------------------------


class SyncCursor(Base):
    __tablename__ = "sync_cursor"
    __table_args__ = (
        _fk(("tenant_id", "venue_id"), "venue"),
        UniqueConstraint("tenant_id", "venue_id", "system", "stream"),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    venue_id: Mapped[UUID]
    system: Mapped[str] = mapped_column(Text)
    stream: Mapped[str] = mapped_column(Text)
    cursor: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# --- Knowledge -----------------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        _fk(("tenant_id", "venue_id"), "venue"),
        UniqueConstraint("tenant_id", "source_system", "source_id"),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    venue_id: Mapped[UUID]
    source_system: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    title: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    blob_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Chunk(Base):
    """A retrievable piece of a document.

    `embedding` has no fixed dimension yet: the embedding model isn't chosen. A later migration sets
    the dimension and adds the HNSW index (with an ADR); until then there is no vector search.
    """

    __tablename__ = "chunk"
    __table_args__ = (
        _fk(("tenant_id", "document_id"), "document", ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "document_id", "ordinal"),
    )

    tenant_id: Mapped[UUID] = _tenant_id()
    id: Mapped[UUID] = _id()
    document_id: Mapped[UUID]
    ordinal: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None]
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    created_at: Mapped[datetime] = _created_at()
