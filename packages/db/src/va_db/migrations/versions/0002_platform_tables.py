"""Platform tables: tenant, venue, app_user, role_assignment, connection, agent_config.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from va_db.migrations.helpers import (
    READ_WRITE,
    check_in,
    drop,
    fk,
    id_,
    protect,
    tenant_fk,
    tenant_id,
    timestamp,
    unique,
)

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        tenant_id(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant"),
        check_in("tenant", "status", ("active", "suspended", "offboarding")),
    )
    # Tenant lifecycle (create, suspend, offboard) belongs to the control plane, not runtime code.
    protect("tenant", ("SELECT",))

    op.create_table(
        "venue",
        tenant_id(),
        id_(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Australia/Sydney"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_venue"),
        tenant_fk("venue"),
        check_in("venue", "status", ("active", "suspended")),
    )
    protect("venue", READ_WRITE)

    op.create_table(
        "app_user",
        tenant_id(),
        id_(),
        sa.Column("external_subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_app_user"),
        tenant_fk("app_user"),
        unique("app_user", "tenant_id", "external_subject"),
        check_in("app_user", "status", ("active", "disabled")),
    )
    protect("app_user", READ_WRITE)

    op.create_table(
        "role_assignment",
        tenant_id(),
        id_(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("venue_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_role_assignment"),
        fk("role_assignment", "user_id", "app_user", ondelete="CASCADE"),
        fk("role_assignment", "venue_id", "venue", ondelete="CASCADE"),
        unique(
            "role_assignment", "tenant_id", "user_id", "venue_id", "role", nulls_not_distinct=True
        ),
        check_in("role_assignment", "role", ("owner", "manager", "approver", "viewer")),
    )
    protect("role_assignment", READ_WRITE)

    op.create_table(
        "connection",
        tenant_id(),
        id_(),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("system", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_connection"),
        fk("connection", "venue_id", "venue"),
        unique("connection", "tenant_id", "venue_id", "system"),
        check_in("connection", "status", ("pending", "active", "error", "revoked")),
    )
    protect("connection", READ_WRITE)

    op.create_table(
        "agent_config",
        tenant_id(),
        id_(),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_agent_config"),
        fk("agent_config", "venue_id", "venue"),
        unique("agent_config", "tenant_id", "venue_id", "agent"),
    )
    protect("agent_config", READ_WRITE)


def downgrade() -> None:
    drop("agent_config", "connection", "role_assignment", "app_user", "venue", "tenant")
