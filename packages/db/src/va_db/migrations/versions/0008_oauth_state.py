"""oauth_state: single-use state for connecting a venue's external system (ADR-0019).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-28

Only a hash of the state value is stored; the PKCE verifier is derived from the state with a
server key, so nothing here can complete a flow. Rows are bound to the user, venue and system that
started the flow and are marked used, not deleted (SELECT, INSERT, UPDATE for the app role).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from va_db.migrations.helpers import drop, fk, id_, protect, tenant_id, timestamp, unique

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_state",
        tenant_id(),
        id_(),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("system", sa.Text(), nullable=False),
        sa.Column("state_sha256", sa.Text(), nullable=False),
        timestamp("expires_at", default_now=False),
        timestamp("used_at", nullable=True, default_now=False),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_oauth_state"),
        fk("oauth_state", "venue_id", "venue", ondelete="CASCADE"),
        fk("oauth_state", "user_id", "app_user", ondelete="CASCADE"),
        unique("oauth_state", "tenant_id", "state_sha256"),
        sa.CheckConstraint(
            "state_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_oauth_state_state_sha256")
        ),
    )
    protect("oauth_state", ("SELECT", "INSERT", "UPDATE"))


def downgrade() -> None:
    drop("oauth_state")
