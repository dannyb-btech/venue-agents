"""Tenant A's rows are invisible and untouchable for tenant B, in every table, and nothing is
visible or writable when no tenant is set."""

from __future__ import annotations

import pytest
from sqlalchemy import Connection, Table, func, select, text
from sqlalchemy.exc import DBAPIError

from tests.isolation.factories import FACTORIES
from tests.isolation.support import Seeded, as_tenant
from va_db.models import Base

TABLES = list(FACTORIES)
# Tables whose rows reference a parent other than the tenant itself.
CHILD_TABLES = [
    name
    for name in TABLES
    if any(fk.column.table.name != "tenant" for fk in Base.metadata.tables[name].foreign_keys)
]
RLS_VIOLATION = "row-level security"
PERMISSION_DENIED = "permission denied"


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _count(conn: Connection, table: Table, **where: object) -> int:
    query = select(func.count()).select_from(table)
    for column, value in where.items():
        query = query.where(table.c[column] == value)
    return conn.execute(query).scalar_one()


def _can(conn: Connection, table: str, privilege: str) -> bool:
    return conn.execute(
        text("SELECT has_table_privilege(current_user, :t, :p)"), {"t": table, "p": privilege}
    ).scalar_one()


def test_every_table_has_a_factory(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        tables = set(
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            ).scalars()
        )
    assert tables == set(FACTORIES), "add a factory in tests/isolation/factories.py"
    assert set(Base.metadata.tables) == tables


@pytest.mark.parametrize("name", TABLES)
def test_admin_sees_both_tenants(seeded: Seeded, name: str) -> None:
    """Sanity check: the data the other tests rely on not seeing is really there."""
    with seeded.admin.connect() as conn:
        assert _count(conn, _table(name), tenant_id=seeded.a.tenant_id) >= 1
        assert _count(conn, _table(name), tenant_id=seeded.b.tenant_id) >= 1


@pytest.mark.parametrize("name", TABLES)
def test_tenant_reads_only_its_own_rows(seeded: Seeded, name: str) -> None:
    table = _table(name)
    with as_tenant(seeded.app, seeded.b.tenant_id) as conn:
        assert _count(conn, table) >= 1
        assert _count(conn, table, tenant_id=seeded.a.tenant_id) == 0
        others = (
            select(func.count()).select_from(table).where(table.c.tenant_id != seeded.b.tenant_id)
        )
        assert conn.execute(others).scalar_one() == 0


@pytest.mark.parametrize("name", TABLES)
def test_tenant_cannot_update_or_delete_other_tenants_rows(seeded: Seeded, name: str) -> None:
    table = _table(name)
    a = seeded.a.tenant_id
    with as_tenant(seeded.app, seeded.b.tenant_id) as conn:
        if _can(conn, name, "UPDATE"):
            result = conn.execute(
                table.update().where(table.c.tenant_id == a).values(tenant_id=table.c.tenant_id)
            )
            assert result.rowcount == 0
        if _can(conn, name, "DELETE"):
            assert conn.execute(table.delete().where(table.c.tenant_id == a)).rowcount == 0
    with seeded.admin.connect() as conn:
        assert _count(conn, table, tenant_id=a) >= 1


@pytest.mark.parametrize("name", TABLES)
def test_tenant_cannot_move_its_rows_to_another_tenant(seeded: Seeded, name: str) -> None:
    table = _table(name)
    with as_tenant(seeded.app, seeded.b.tenant_id) as conn:
        if not _can(conn, name, "UPDATE"):
            pytest.skip(f"app role has no UPDATE on {name}")
        # The policy's WITH CHECK runs before FK/PK checks, so this is RLS rejecting it.
        with pytest.raises(DBAPIError, match=RLS_VIOLATION):
            conn.execute(table.update().values(tenant_id=seeded.a.tenant_id))


def _insert_rejection(seeded: Seeded, name: str) -> str:
    """Error expected when an insert must fail: RLS, or no INSERT grant at all."""
    with seeded.app.connect() as conn:
        return RLS_VIOLATION if _can(conn, name, "INSERT") else PERMISSION_DENIED


@pytest.mark.parametrize("name", TABLES)
def test_tenant_cannot_insert_rows_for_another_tenant(seeded: Seeded, name: str) -> None:
    row = FACTORIES[name](seeded.a.tenant_id, seeded.a.refs)
    with (
        as_tenant(seeded.app, seeded.b.tenant_id) as conn,
        pytest.raises(DBAPIError, match=_insert_rejection(seeded, name)),
    ):
        conn.execute(_table(name).insert().values(**row))


@pytest.mark.parametrize("name", CHILD_TABLES)
def test_tenant_cannot_reference_another_tenants_rows(seeded: Seeded, name: str) -> None:
    """Composite FKs: a tenant-B row pointing at tenant A's parents doesn't satisfy any FK."""
    row = FACTORIES[name](seeded.b.tenant_id, seeded.a.refs)
    with (
        as_tenant(seeded.app, seeded.b.tenant_id) as conn,
        pytest.raises(DBAPIError, match="foreign key"),
    ):
        conn.execute(_table(name).insert().values(**row))


@pytest.mark.parametrize("tenant_setting", [None, ""])
@pytest.mark.parametrize("name", TABLES)
def test_no_tenant_set_fails_closed(seeded: Seeded, name: str, tenant_setting: str | None) -> None:
    table = _table(name)
    with seeded.app.connect() as conn, conn.begin() as tx:
        if tenant_setting is not None:
            conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_setting}
            )
        assert _count(conn, table) == 0
        if _can(conn, name, "UPDATE"):
            assert conn.execute(table.update().values(tenant_id=table.c.tenant_id)).rowcount == 0
        if _can(conn, name, "DELETE"):
            assert conn.execute(table.delete()).rowcount == 0
        tx.rollback()
    row = FACTORIES[name](seeded.b.tenant_id, seeded.b.refs)
    with (
        as_tenant(seeded.app, None) as conn,
        pytest.raises(DBAPIError, match=_insert_rejection(seeded, name)),
    ):
        conn.execute(table.insert().values(**row))


def test_tenant_id_defaults_to_the_session_tenant(seeded: Seeded) -> None:
    row = FACTORIES["venue"](seeded.b.tenant_id, seeded.b.refs)
    del row["tenant_id"]
    table = _table("venue")
    with as_tenant(seeded.app, seeded.b.tenant_id) as conn:
        stored = conn.execute(table.insert().values(**row).returning(table.c.tenant_id))
        assert stored.scalar_one() == seeded.b.tenant_id


def test_omitted_tenant_id_without_a_session_tenant_is_rejected(seeded: Seeded) -> None:
    row = FACTORIES["venue"](seeded.b.tenant_id, seeded.b.refs)
    del row["tenant_id"]
    # Postgres checks the RLS policy before NOT NULL; either way the row is rejected.
    with (
        as_tenant(seeded.app, None) as conn,
        pytest.raises(DBAPIError, match=f"{RLS_VIOLATION}|null value"),
    ):
        conn.execute(_table("venue").insert().values(**row))


def test_app_role_cannot_rewrite_the_audit_trail(seeded: Seeded) -> None:
    """Approvals, tool calls and usage are append-only for runtime code (ADR-0006)."""
    with seeded.app.connect() as conn:
        for name in ("approval", "tool_call", "usage_cost"):
            assert _can(conn, name, "SELECT")
            assert _can(conn, name, "INSERT")
            assert not _can(conn, name, "UPDATE")
            assert not _can(conn, name, "DELETE")
        assert not _can(conn, "action_log", "DELETE")
