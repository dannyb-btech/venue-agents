"""Tenant-scoped database sessions.

`tenant_session` is the only way request and agent code should talk to the stamp database. It
takes the `TenantContext` resolved at the edge and sets `app.tenant_id` with
`set_config(..., true)` at the start of every transaction, so RLS applies to every statement.

Defences on the runtime engine (`create_engine` / `create_runtime_engine`):
- the setting is transaction-local, and connections run `RESET ALL` when they go back to the pool,
  so no session state (tenant, role, search_path) reaches the next user;
- a statement guard rejects any other statement that touches `app.tenant_id`, `set_config`, roles
  or session state, so a stray or injected statement can't switch tenant mid-transaction;
- `assert_rls_enforced` refuses a login that could bypass RLS. Call it at service start-up.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import TupleRow
from pydantic import SecretStr
from sqlalchemy import URL, Connection, event, make_url, text
from sqlalchemy.engine import ExecutionContext
from sqlalchemy.engine.interfaces import DBAPIConnection, DBAPICursor
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.pool import ConnectionPoolEntry, PoolResetState

from va_core.errors import ConfigurationError, TenantContextError
from va_core.settings import CoreSettings
from va_core.tenancy import TenantContext, TenantIdentity
from va_db.rls import APP_ROLE, GROUP_ROLES

PasswordProvider = Callable[[], Awaitable[SecretStr]]
"""Returns the password for a new connection (local secret or Entra access token)."""

_SET_TENANT_OPTION = "va_set_tenant"
_SET_TENANT = text("SELECT set_config('app.tenant_id', :tenant_id, true)").execution_options(
    **{_SET_TENANT_OPTION: True}
)
_FORBIDDEN = re.compile(
    r"app\.tenant_id|set_config|\bset\s+(local\s+|session\s+)?role\b|session\s+authorization"
    r"|\breset\b|\bdiscard\b|\bset\s+(local\s+|session\s+)?search_path\b",
    re.IGNORECASE,
)
_ROLE_CHECK = text(
    """
    SELECT
        EXISTS (
            SELECT FROM pg_roles r
            WHERE pg_has_role(current_user, r.oid, 'MEMBER') AND (r.rolsuper OR r.rolbypassrls)
        ) AS can_bypass,
        pg_has_role(current_user, :app_role, 'MEMBER') AS is_app,
        EXISTS (
            SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND pg_has_role(current_user, c.relowner, 'MEMBER')
        ) AS owns_tables,
        ARRAY(
            SELECT r.rolname FROM pg_roles r
            WHERE r.rolname = ANY(:groups) AND r.rolname <> :app_role
              AND pg_has_role(current_user, r.oid, 'MEMBER')
            ORDER BY r.rolname
        ) AS other_groups
    """
)


class ForbiddenStatementError(TenantContextError):
    """Runtime code tried to change the tenant, role or session state outside `tenant_session`."""


def _guard(
    _conn: Connection,
    _cursor: DBAPICursor,
    statement: str,
    _parameters: object,
    context: ExecutionContext | None,
    _executemany: bool,
) -> None:
    if context is not None and context.execution_options.get(_SET_TENANT_OPTION):
        return
    if _FORBIDDEN.search(statement):
        raise ForbiddenStatementError(
            "statements that change the tenant, role or session state are not allowed; "
            "use tenant_session"
        )


def _reset_session_state(
    dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry, state: PoolResetState
) -> None:
    # Runs after the pool's rollback. Skip when the connection is being discarded anyway.
    if state.terminate_only or not state.asyncio_safe:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("RESET ALL")
    finally:
        cursor.close()
    # The driver opened a transaction for RESET ALL; end it so the connection goes back idle.
    dbapi_connection.commit()


def create_runtime_engine(
    url: str | URL,
    *,
    connect_timeout: int = 10,
    pool_size: int = 5,
    max_overflow: int = 10,
    password: PasswordProvider | None = None,
    pool_recycle: int = -1,
) -> AsyncEngine:
    """An engine with the runtime defences. Pass a runtime role's URL, never the admin's.

    With `password`, the URL carries no password: each new connection awaits the provider (a local
    secret, or an Entra token in Azure; see `va_db.credentials`), so expiring tokens are never
    reused for a new login.
    """
    kwargs: dict[str, object] = {}
    if password is not None:
        parsed = make_url(url)
        if parsed.password is not None:
            raise ConfigurationError("pass the password in the URL or as a provider, not both")
        options = {key: str(value) for key, value in parsed.query.items()}
        if {"password", "passfile"} & options.keys():
            raise ConfigurationError("pass the password as a provider, not in the URL query")

        async def connect() -> psycopg.AsyncConnection[TupleRow]:
            secret = await password()
            conninfo = make_conninfo(
                host=parsed.host,
                port=parsed.port or 5432,
                dbname=parsed.database,
                user=parsed.username,
                connect_timeout=connect_timeout,
                **options,
            )
            # The secret goes as a keyword, never into a string that could be logged.
            return await psycopg.AsyncConnection.connect(
                conninfo, password=secret.get_secret_value()
            )

        kwargs["async_creator"] = connect
    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        # Errors must not carry bound values (names, payloads) into logs and traces.
        hide_parameters=True,
        connect_args={"connect_timeout": connect_timeout},
        **kwargs,
    )
    event.listen(engine.sync_engine, "before_cursor_execute", _guard)
    event.listen(engine.sync_engine, "reset", _reset_session_state)
    return engine


def create_engine(settings: CoreSettings) -> AsyncEngine:
    """Runtime engine from `DATABASE_URL`."""
    if settings.database_url is None:
        raise ConfigurationError("DATABASE_URL is not set")
    return create_runtime_engine(
        settings.database_url.get_secret_value(),
        connect_timeout=settings.database_connect_timeout,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def assert_rls_enforced(engine: AsyncEngine, role: str = APP_ROLE) -> None:
    """Fail start-up unless the login is a member of `role` that can't bypass RLS.

    Checks every role the login can reach (a member of a superuser or BYPASSRLS role could
    `SET ROLE` to it), that it owns no tables (owners can disable RLS) and that it is in no other
    platform group role. `role` is `va_app` for runtime services, `va_provision` / `va_catalog`
    for the control plane.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(_ROLE_CHECK, {"app_role": role, "groups": list(GROUP_ROLES)})
        ).one()
    if row.can_bypass:
        raise ConfigurationError(
            f"database login bypasses row-level security; use a {role} member, not the admin role"
        )
    if row.owns_tables:
        raise ConfigurationError(f"database login owns tables; use a {role} member")
    if not row.is_app:
        raise ConfigurationError(f"database login is not a member of {role}")
    if row.other_groups:
        raise ConfigurationError(
            f"database login for {role} is also a member of {', '.join(row.other_groups)}; "
            "use one login per group role"
        )


@asynccontextmanager
async def tenant_session(
    sessionmaker: async_sessionmaker[AsyncSession], ctx: TenantContext | TenantIdentity
) -> AsyncGenerator[AsyncSession]:
    """A session whose every transaction runs as `ctx.tenant_id`.

    `ctx` is a `TenantContext`, or a `TenantIdentity` for tenant-level reads before a venue is
    chosen (ADR-0019). Both are only ever built from an authenticated token or envelope.

    Work left in the session commits when the block exits normally and rolls back on error.
    Explicit `commit()` inside the block is allowed (e.g. record "pending" before an external
    call): every new transaction sets the tenant again.
    """
    if not isinstance(ctx, TenantContext | TenantIdentity):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TenantContextError("tenant_session requires a TenantContext or TenantIdentity")
    tenant_id = str(ctx.tenant_id)

    def set_tenant(_session: Session, _tx: SessionTransaction, connection: Connection) -> None:
        connection.execute(_SET_TENANT, {"tenant_id": tenant_id})

    async with sessionmaker() as session:
        event.listen(session.sync_session, "after_begin", set_tenant)
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        await session.commit()
