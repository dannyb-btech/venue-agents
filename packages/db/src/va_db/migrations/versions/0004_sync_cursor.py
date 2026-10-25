"""Sync state: sync_cursor.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from va_db.migrations.helpers import (
    READ_WRITE,
    drop,
    fk,
    id_,
    protect,
    tenant_id,
    timestamp,
    unique,
)

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_cursor",
        tenant_id(),
        id_(),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("system", sa.Text(), nullable=False),
        sa.Column("stream", sa.Text(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        timestamp("last_success_at", nullable=True, default_now=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_sync_cursor"),
        fk("sync_cursor", "venue_id", "venue"),
        unique("sync_cursor", "tenant_id", "venue_id", "system", "stream"),
    )
    protect("sync_cursor", READ_WRITE)


def downgrade() -> None:
    drop("sync_cursor")
