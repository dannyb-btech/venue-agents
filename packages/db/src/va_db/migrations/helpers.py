"""Column and constraint builders for migrations.

Applied migrations import these, so changing what a helper produces rewrites history: add a new
helper instead. Constraint names follow `va_db.models.NAMING_CONVENTION`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op

from va_db.rls import revoke_table_sql, tenant_table_sql

READ_WRITE = ("SELECT", "INSERT", "UPDATE", "DELETE")
APPEND_ONLY = ("SELECT", "INSERT")


def tenant_id(*, primary_key: bool = True) -> sa.Column[UUID]:
    return sa.Column(
        "tenant_id",
        sa.Uuid(),
        primary_key=primary_key,
        nullable=False,
        server_default=sa.text("va_current_tenant()"),
    )


def id_() -> sa.Column[UUID]:
    return sa.Column("id", sa.Uuid(), primary_key=True, nullable=False)


def timestamp(
    name: str, *, nullable: bool = False, default_now: bool = True
) -> sa.Column[datetime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def fk(
    table: str, column: str, referred: str, *, ondelete: str | None = None
) -> sa.ForeignKeyConstraint:
    """Composite `(tenant_id, column) -> referred(tenant_id, id)`."""
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{referred}.tenant_id", f"{referred}.id"],
        name=f"fk_{table}_tenant_id_{column}_{referred}",
        ondelete=ondelete,
    )


def tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["tenant.tenant_id"], name=f"fk_{table}_tenant_id_tenant"
    )


def unique(table: str, *columns: str, nulls_not_distinct: bool = False) -> sa.UniqueConstraint:
    return sa.UniqueConstraint(
        *columns,
        name=f"uq_{table}_{'_'.join(columns)}",
        postgresql_nulls_not_distinct=nulls_not_distinct,
    )


def check_in(table: str, column: str, values: Iterable[str]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{table}_{column}")


def index(table: str, *columns: str) -> None:
    op.create_index(f"ix_{table}_{'_'.join(columns)}", table, list(columns))


def protect(table: str, privileges: Iterable[str]) -> None:
    """Enable + force tenant RLS on `table` and grant `privileges` to the app role."""
    for statement in tenant_table_sql(table, privileges):
        op.execute(statement)


def drop(*tables: str) -> None:
    """Drop tables (children first). Their policies go with them."""
    for table in tables:
        op.execute(revoke_table_sql(table))
        op.drop_table(table)
