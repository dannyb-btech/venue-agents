"""Postgres implementations of the connector framework's stores (credentials, drafts).

Every query runs in a `tenant_session` for the context taken from the verified run envelope, so RLS
applies: a connector can only see its own tenant's connections and write its own tenant's drafts.
Connectors log in as a `va_app` member until per-service roles land (P0-08 prerequisite H1).
This module needs no `mcp` extra; `va_db.connector_deps` wires a connector server.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from va_core.credentials import ConnectionInfo
from va_core.errors import AuthorizationError, ExternalServiceError
from va_core.mcp.drafts import NewDraft
from va_core.tenancy import TenantContext
from va_db.ids import uuid7
from va_db.models import AgentRun, Connection, Draft, DraftStatus, RunStatus
from va_db.session import tenant_session

_TRY_REFRESH_LOCK = text("SELECT pg_try_advisory_xact_lock(hashtext('va-refresh'), hashtext(:key))")
_OPEN_RUNS = frozenset({RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING_APPROVAL})


class SqlDraftWriter:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(self, ctx: TenantContext, run_id: UUID, draft: NewDraft) -> UUID:
        """Insert a pending draft. The run must be this venue's and still open."""
        draft_id = uuid7()
        async with tenant_session(self._sessionmaker, ctx) as session:
            run = (
                await session.execute(
                    select(AgentRun.venue_id, AgentRun.status).where(AgentRun.id == run_id)
                )
            ).one_or_none()
            if run is None or run.venue_id != ctx.venue_id:
                raise AuthorizationError("the run does not belong to this venue")
            if run.status not in _OPEN_RUNS:
                raise AuthorizationError("the run has finished; it can't create drafts")
            session.add(
                Draft(
                    tenant_id=ctx.tenant_id,
                    id=draft_id,
                    venue_id=ctx.venue_id,
                    run_id=run_id,
                    type=draft.type,
                    status=DraftStatus.PENDING.value,
                    payload=draft.payload,
                    reasoning_summary=draft.reasoning_summary,
                    sources=draft.sources,
                )
            )
        return draft_id


class SqlConnectionStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, ctx: TenantContext, system: str) -> ConnectionInfo | None:
        async with tenant_session(self._sessionmaker, ctx) as session:
            row = await session.scalar(
                select(Connection).where(
                    Connection.venue_id == ctx.venue_id, Connection.system == system
                )
            )
            if row is None:
                return None
            return ConnectionInfo(
                system=row.system,
                status=row.status,
                scopes=tuple(row.scopes),
                secret_ref=row.secret_ref,
            )


class PgRefreshLock:
    """A transaction-scoped advisory lock per (tenant, credential), held while a token refreshes.

    Waits at most `wait` seconds (polling `pg_try_advisory_xact_lock`) so waiters can't pile up on
    the pool. The lock holds one pooled connection for the refresh: the refresh must not use the DB.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        wait: float = 20.0,
        poll: float = 0.1,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._wait = wait
        self._poll = poll

    @asynccontextmanager
    async def hold(self, ctx: TenantContext, secret_ref: str) -> AsyncGenerator[None]:
        key = f"{ctx.tenant_id}:{secret_ref}"
        deadline = time.monotonic() + self._wait
        async with tenant_session(self._sessionmaker, ctx) as session:
            while not (await session.execute(_TRY_REFRESH_LOCK, {"key": key})).scalar_one():
                if time.monotonic() >= deadline:
                    raise ExternalServiceError(
                        "timed out waiting for another credential refresh",
                        service="refresh_lock",
                        retryable=True,
                    )
                await asyncio.sleep(self._poll)
            yield
