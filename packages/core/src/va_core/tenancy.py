"""Tenant context and its propagation.

The tenant is resolved once, at the edge (auth token or signed run envelope), into a
`TenantContext`. Never build one from a request body, query string, tool argument or model output.

Pass the context object explicitly to anything that touches data (DB sessions, secrets). The
context variables here exist so telemetry and logging can tag every span and record with the
venue and run without threading arguments through every call. They follow `await`, `gather` and
`create_task` within a process, but not process or orchestration boundaries: durable activities
must receive the context in their (signed) input and re-enter `tenant_scope`.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from va_core.errors import TenantContextError


class Role(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    APPROVER = "approver"
    VIEWER = "viewer"


class TenantContext(BaseModel):
    """Who is acting, for which tenant and venue, on which stamp. Immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID
    venue_id: UUID
    stamp_id: str = Field(min_length=1)
    user_id: str | None = None
    roles: frozenset[Role] = frozenset()

    def has_role(self, *roles: Role) -> bool:
        """True if the context holds any of `roles`."""
        return not self.roles.isdisjoint(roles)


_tenant: ContextVar[TenantContext | None] = ContextVar("va_tenant", default=None)
_run_id: ContextVar[str | None] = ContextVar("va_run_id", default=None)
# Run IDs end up in logs and span attributes: keep them to safe characters (no log injection).
_RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@contextmanager
def tenant_scope(ctx: TenantContext) -> Generator[TenantContext]:
    """Set the current tenant for the duration of the block.

    Re-entering with an identical context is allowed. Any change inside a scope (tenant, venue,
    stamp, user or roles) raises `TenantContextError`, so one request or run can never act for two
    tenants or widen its own roles.
    """
    existing = _tenant.get()
    if existing is not None and existing != ctx:
        raise TenantContextError("cannot change the tenant context inside an active tenant scope")
    token = _tenant.set(ctx)
    try:
        yield ctx
    finally:
        _tenant.reset(token)


def current_tenant() -> TenantContext:
    """The active tenant. Raises `TenantContextError` when none is set (fail closed)."""
    ctx = _tenant.get()
    if ctx is None:
        raise TenantContextError("no tenant context is set")
    return ctx


def current_tenant_or_none() -> TenantContext | None:
    """The active tenant, or None. For telemetry only; data access must use `current_tenant()`."""
    return _tenant.get()


@contextmanager
def run_scope(run_id: str) -> Generator[str]:
    """Tag everything in the block with an agent `run_id`."""
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be 1-128 letters, digits, '_', '.', ':' or '-'")
    token = _run_id.set(run_id)
    try:
        yield run_id
    finally:
        _run_id.reset(token)


def current_run_id() -> str | None:
    return _run_id.get()
