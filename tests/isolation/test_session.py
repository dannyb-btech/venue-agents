"""`tenant_session`: the tenant comes from `TenantContext` and never leaks via the pool."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import LiteralString, cast
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.isolation.support import Seeded
from va_core.errors import ConfigurationError, TenantContextError
from va_core.tenancy import TenantContext
from va_db.ids import uuid7
from va_db.models import SyncCursor, Venue
from va_db.session import (
    ForbiddenStatementError,
    assert_rls_enforced,
    create_runtime_engine,
    create_sessionmaker,
    tenant_session,
)


def _ctx(tenant_id: UUID, venue_id: UUID) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, venue_id=venue_id, stamp_id="canary-local")


@pytest.fixture
async def engine(seeded: Seeded) -> AsyncGenerator[AsyncEngine]:
    # One pooled connection, so every session reuses it: proves nothing leaks between users.
    url = seeded.db.app_url.render_as_string(hide_password=False)
    engine = create_runtime_engine(url, pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()


async def _raw(conn: AsyncConnection, sql: str) -> object:
    """Run SQL on the driver connection, bypassing the statement guard (as buggy code might)."""
    raw = await conn.get_raw_connection()
    driver = raw.driver_connection
    assert isinstance(driver, psycopg.AsyncConnection)
    cursor = await driver.execute(cast(LiteralString, sql))  # test-only SQL written in this file
    row = await cursor.fetchone() if cursor.description else None
    return row[0] if row else None


async def _raw_commit(conn: AsyncConnection) -> None:
    raw = await conn.get_raw_connection()
    driver = raw.driver_connection
    assert isinstance(driver, psycopg.AsyncConnection)
    await driver.commit()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(engine)


async def _venue_ids(session: AsyncSession) -> set[UUID]:
    return set((await session.execute(select(Venue.id))).scalars())


async def test_session_sees_only_its_tenant(
    seeded: Seeded, sessions: async_sessionmaker[AsyncSession]
) -> None:
    a, b = seeded.a, seeded.b
    async with tenant_session(sessions, _ctx(a.tenant_id, a.refs["venue"])) as session:
        assert await _venue_ids(session) == {a.refs["venue"]}
    async with tenant_session(sessions, _ctx(b.tenant_id, b.refs["venue"])) as session:
        assert await _venue_ids(session) == {b.refs["venue"]}


async def test_tenant_does_not_leak_to_the_next_pool_user(
    seeded: Seeded, engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    a = seeded.a
    async with tenant_session(sessions, _ctx(a.tenant_id, a.refs["venue"])) as session:
        assert await _venue_ids(session)
    with pytest.raises(RuntimeError):
        async with tenant_session(sessions, _ctx(a.tenant_id, a.refs["venue"])) as session:
            assert await _venue_ids(session)
            raise RuntimeError("boom")
    async with engine.connect() as conn:
        assert (await conn.execute(select(func.count()).select_from(Venue))).scalar_one() == 0
        assert await _raw(conn, "SELECT current_setting('app.tenant_id', true)") in (None, "")


async def test_session_level_state_is_reset_on_checkin(seeded: Seeded, engine: AsyncEngine) -> None:
    """Even a session-level (not transaction-local) setting doesn't survive the pool."""
    a = seeded.a
    async with engine.connect() as conn:
        await _raw(conn, f"SELECT set_config('app.tenant_id', '{a.tenant_id}', false)")
        await _raw(conn, "SET search_path TO pg_temp, public")
        # Committed, so the pool's rollback on checkin can't undo it (a rolled-back SET reverts).
        await _raw_commit(conn)
        assert (await conn.execute(select(func.count()).select_from(Venue))).scalar_one() >= 1
    async with engine.connect() as conn:
        assert (await conn.execute(select(func.count()).select_from(Venue))).scalar_one() == 0
        assert await _raw(conn, "SELECT current_setting('app.tenant_id', true)") in (None, "")
        assert await _raw(conn, "SHOW search_path") == '"$user", public'


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT set_config('app.tenant_id', '0193a000-0000-7000-8000-00000000000a', true)",
        "SET LOCAL app.tenant_id = '0193a000-0000-7000-8000-00000000000a'",
        "set   app.TENANT_ID to 'x'",
        "SET ROLE va_app",
        "RESET ALL",
        "SET search_path TO pg_temp, public",
        "SELECT current_setting('app.tenant_id')",
    ],
)
async def test_guard_rejects_tenant_and_session_statements(
    seeded: Seeded, sessions: async_sessionmaker[AsyncSession], statement: str
) -> None:
    b = seeded.b
    with pytest.raises(ForbiddenStatementError):
        async with tenant_session(sessions, _ctx(b.tenant_id, b.refs["venue"])) as session:
            await session.execute(text(statement))


async def test_tenant_is_reapplied_after_commit_inside_the_block(
    seeded: Seeded, sessions: async_sessionmaker[AsyncSession]
) -> None:
    a = seeded.a
    async with tenant_session(sessions, _ctx(a.tenant_id, a.refs["venue"])) as session:
        await session.commit()
        assert await _venue_ids(session) == {a.refs["venue"]}


async def test_orm_insert_takes_tenant_from_context(
    seeded: Seeded, sessions: async_sessionmaker[AsyncSession]
) -> None:
    b = seeded.b
    venue = Venue(name="Second room")
    async with tenant_session(sessions, _ctx(b.tenant_id, b.refs["venue"])) as session:
        session.add(venue)
        await session.flush()
        assert venue.tenant_id == b.tenant_id
        await session.rollback()


async def test_commits_on_exit_and_rolls_back_on_error(
    seeded: Seeded, sessions: async_sessionmaker[AsyncSession]
) -> None:
    b = seeded.b
    ctx = _ctx(b.tenant_id, b.refs["venue"])
    kept, dropped = f"kept-{uuid7().hex}", f"dropped-{uuid7().hex}"
    async with tenant_session(sessions, ctx) as session:
        session.add(SyncCursor(venue_id=b.refs["venue"], system="tanda", stream=kept))
    with pytest.raises(RuntimeError):
        async with tenant_session(sessions, ctx) as session:
            session.add(SyncCursor(venue_id=b.refs["venue"], system="tanda", stream=dropped))
            await session.flush()
            raise RuntimeError("boom")
    with seeded.admin.begin() as conn:
        streams = set(
            conn.execute(
                select(SyncCursor.stream).where(SyncCursor.stream.in_([kept, dropped]))
            ).scalars()
        )
        conn.execute(delete(SyncCursor).where(SyncCursor.stream == kept))
    assert streams == {kept}


async def test_requires_a_tenant_context(sessions: async_sessionmaker[AsyncSession]) -> None:
    with pytest.raises(TenantContextError):
        async with tenant_session(sessions, "0193a000-0000-7000-8000-00000000000a"):  # pyright: ignore[reportArgumentType]
            pass


async def test_assert_rls_enforced(seeded: Seeded, engine: AsyncEngine) -> None:
    await assert_rls_enforced(engine)
    admin = create_async_engine(seeded.db.admin_url)
    try:
        with pytest.raises(ConfigurationError, match="bypasses row-level security"):
            await assert_rls_enforced(admin)
    finally:
        await admin.dispose()
