"""Test support: throwaway migrated databases with a throwaway `va_app` login.

Used by `tests/isolation` and `packages/db/tests`. Needs an admin URL for a server you can create
databases and roles on (local compose or the CI service container). Never point it at a stamp.
"""

from __future__ import annotations

import secrets
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, make_url, pool

from va_core.errors import ConfigurationError
from va_db.rls import APP_ROLE, CREATE_APP_ROLE

MIGRATIONS = Path(__file__).parent / "migrations"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class TempDatabase:
    name: str
    admin_url: URL
    """Schema owner (superuser locally): bypasses RLS. Setup and assertions only."""
    app_url: URL
    """A `va_app` member: what runtime code connects as."""

    def admin_engine(self) -> Engine:
        return create_engine(self.admin_url, poolclass=pool.NullPool)

    def app_engine(self) -> Engine:
        return create_engine(self.app_url, poolclass=pool.NullPool)


def migrate(url: URL, revision: str, *, down: bool = False) -> None:
    """Run Alembic up (or down) to `revision` against `url`."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            if down:
                command.downgrade(cfg, revision)
            else:
                command.upgrade(cfg, revision)
            conn.commit()
    finally:
        engine.dispose()


@contextmanager
def temp_database(admin_url: str, *, revision: str | None = "head") -> Generator[TempDatabase]:
    """Create a fresh database (migrated to `revision`, or empty if None) and a login for it.

    The login's random password is inlined in the CREATE ROLE statement (DDL takes no parameters),
    so it can appear in local server logs or error text: acceptable for a throwaway local role.
    """
    server_url = make_url(admin_url)
    if server_url.host not in LOCAL_HOSTS:
        raise ConfigurationError(
            f"temp_database creates and drops databases and roles: local servers only "
            f"({', '.join(sorted(LOCAL_HOSTS))}), not {server_url.host!r}"
        )
    suffix = secrets.token_hex(6)
    name = f"va_test_{suffix}"
    login = f"va_test_login_{suffix}"
    password = secrets.token_hex(16)  # hex only: safe to inline in DDL
    server = create_engine(server_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool)
    with server.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
        conn.exec_driver_sql(CREATE_APP_ROLE)
        conn.exec_driver_sql(f"CREATE ROLE {login} LOGIN PASSWORD '{password}' IN ROLE {APP_ROLE}")
    admin = server_url.set(database=name)
    try:
        if revision is not None:
            migrate(admin, revision)
        yield TempDatabase(
            name=name,
            admin_url=admin,
            app_url=admin.set(username=login, password=password),
        )
    finally:
        with server.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.exec_driver_sql(f"DROP ROLE IF EXISTS {login}")
        server.dispose()
