"""Test support: throwaway migrated databases with throwaway logins.

Used by `tests/isolation`, `packages/db/tests` and the control plane's tests. Needs an admin URL
for a server you can create databases and roles on (local compose or the CI service container).
Never point it at a stamp or the catalog.
"""

from __future__ import annotations

import secrets
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, make_url, pool

from va_core.errors import ConfigurationError
from va_db.rls import APP_ROLE, CATALOG_ROLE, create_group_role_sql

MIGRATIONS = Path(__file__).parent / "migrations"
CATALOG_MIGRATIONS = Path(__file__).parent / "catalog" / "migrations"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class TempDatabase:
    name: str
    admin_url: URL
    """Schema owner (superuser locally): bypasses RLS. Setup and assertions only."""
    app_url: URL
    """The first login's URL (a `va_app` member for a stamp): what runtime code connects as."""
    logins: dict[str, URL] = field(default_factory=dict[str, URL])
    """Group role -> URL of a login that is a member of it."""

    def admin_engine(self) -> Engine:
        return create_engine(self.admin_url, poolclass=pool.NullPool)

    def app_engine(self) -> Engine:
        return create_engine(self.app_url, poolclass=pool.NullPool)


def migrate(url: URL, revision: str, *, down: bool = False, migrations: Path = MIGRATIONS) -> None:
    """Run Alembic up (or down) to `revision` against `url` (stamp tree unless `migrations`)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations))
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
def temp_database(
    admin_url: str,
    *,
    revision: str | None = "head",
    migrations: Path = MIGRATIONS,
    logins: Sequence[str] = (APP_ROLE,),
) -> Generator[TempDatabase]:
    """Create a fresh database (migrated to `revision`, or empty if None) and logins for it.

    One login per group role in `logins` (created first if missing; the migrations grant to it).
    Login passwords are random and inlined in the CREATE ROLE statement (DDL takes no parameters),
    so they can appear in local server logs or error text: acceptable for throwaway local roles.
    """
    server_url = make_url(admin_url)
    if server_url.host not in LOCAL_HOSTS:
        raise ConfigurationError(
            f"temp_database creates and drops databases and roles: local servers only "
            f"({', '.join(sorted(LOCAL_HOSTS))}), not {server_url.host!r}"
        )
    if not logins:
        raise ValueError("temp_database needs at least one login role")
    suffix = secrets.token_hex(6)
    name = f"va_test_{suffix}"
    admin = server_url.set(database=name)
    created: dict[str, URL] = {}
    server = create_engine(server_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool)
    try:
        with server.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
            for i, role in enumerate(logins):
                login = f"va_test_login_{suffix}_{i}"
                password = secrets.token_hex(16)  # hex only: safe to inline in DDL
                conn.exec_driver_sql(create_group_role_sql(role))
                conn.exec_driver_sql(
                    f"CREATE ROLE {login} LOGIN PASSWORD '{password}' IN ROLE {role}"
                )
                created[role] = admin.set(username=login, password=password)
        if revision is not None:
            migrate(admin, revision, migrations=migrations)
        yield TempDatabase(name=name, admin_url=admin, app_url=created[logins[0]], logins=created)
    finally:
        with server.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            for url in created.values():
                conn.exec_driver_sql(f"DROP ROLE IF EXISTS {url.username}")
        server.dispose()


@contextmanager
def temp_catalog(admin_url: str, *, revision: str | None = "head") -> Generator[TempDatabase]:
    """A throwaway catalog database with one `va_catalog` login."""
    with temp_database(
        admin_url, revision=revision, migrations=CATALOG_MIGRATIONS, logins=(CATALOG_ROLE,)
    ) as db:
        yield db
