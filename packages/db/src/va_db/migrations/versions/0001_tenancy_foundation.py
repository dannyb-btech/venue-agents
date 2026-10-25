"""Tenancy foundation: pgvector, the va_app role and va_current_tenant().

Revision ID: 0001
Revises:
Create Date: 2026-10-25

The downgrade leaves the `va_app` role and the `vector` extension in place: roles are cluster-wide
(other databases on the server may use it) and the extension is created by the platform (Azure
allow-list / local init.sql).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from va_db.rls import APP_ROLE, CREATE_APP_ROLE, CREATE_CURRENT_TENANT_FN, DROP_CURRENT_TENANT_FN

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(CREATE_APP_ROLE)
    # PG15+ already withholds CREATE on public from PUBLIC; don't rely on server defaults.
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(CREATE_CURRENT_TENANT_FN)


def downgrade() -> None:
    op.execute(DROP_CURRENT_TENANT_FN)
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    # CREATE on public stays revoked: the PG15+ default, and safer.
