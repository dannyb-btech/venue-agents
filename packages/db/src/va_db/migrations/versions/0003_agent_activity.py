"""Agent activity: agent_run, tool_call, draft, approval, action_log, usage_cost.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25

`tool_call`, `approval` and `usage_cost` are append-only for the app role (audit trail).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from va_db.migrations.helpers import (
    APPEND_ONLY,
    READ_WRITE,
    check_in,
    drop,
    fk,
    id_,
    index,
    protect,
    tenant_id,
    timestamp,
    unique,
)

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_run",
        tenant_id(),
        id_(),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.Text(), nullable=True),
        timestamp("started_at"),
        timestamp("ended_at", nullable=True, default_now=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_agent_run"),
        fk("agent_run", "venue_id", "venue"),
        check_in(
            "agent_run",
            "status",
            ("pending", "running", "waiting_approval", "succeeded", "failed", "cancelled"),
        ),
    )
    index("agent_run", "tenant_id", "venue_id", "started_at")
    protect("agent_run", READ_WRITE)

    op.create_table(
        "tool_call",
        tenant_id(),
        id_(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("args_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_tool_call"),
        fk("tool_call", "run_id", "agent_run"),
        check_in("tool_call", "category", ("read", "draft")),
        check_in("tool_call", "status", ("succeeded", "failed")),
    )
    index("tool_call", "tenant_id", "run_id")
    protect("tool_call", APPEND_ONLY)

    op.create_table(
        "draft",
        tenant_id(),
        id_(),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("reasoning_summary", sa.Text(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=False, server_default="[]"),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_draft"),
        fk("draft", "venue_id", "venue"),
        fk("draft", "run_id", "agent_run"),
        check_in(
            "draft",
            "status",
            ("pending", "approved", "rejected", "superseded", "executed", "failed"),
        ),
    )
    index("draft", "tenant_id", "venue_id", "status")
    protect("draft", READ_WRITE)

    op.create_table(
        "approval",
        tenant_id(),
        id_(),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("edits", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        timestamp("decided_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_approval"),
        fk("approval", "draft_id", "draft"),
        fk("approval", "user_id", "app_user"),
        unique("approval", "tenant_id", "draft_id"),
        check_in("approval", "decision", ("approved", "rejected")),
    )
    protect("approval", APPEND_ONLY)

    op.create_table(
        "action_log",
        tenant_id(),
        id_(),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default="{}"),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_action_log"),
        fk("action_log", "approval_id", "approval"),
        fk("action_log", "draft_id", "draft"),
        unique("action_log", "tenant_id", "approval_id"),
        sa.CheckConstraint(
            "approval_id IS NOT NULL OR status = 'refused'", name="ck_action_log_approval_required"
        ),
        check_in(
            "action_log",
            "status",
            ("pending", "succeeded", "failed", "refused", "outcome_unknown"),
        ),
    )
    protect("action_log", ("SELECT", "INSERT", "UPDATE"))

    op.create_table(
        "usage_cost",
        tenant_id(),
        id_(),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Numeric(14, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="AUD"),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_usage_cost"),
        fk("usage_cost", "venue_id", "venue"),
        fk("usage_cost", "run_id", "agent_run"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cached_input_tokens >= 0",
            name="ck_usage_cost_tokens_non_negative",
        ),
    )
    index("usage_cost", "tenant_id", "venue_id", "created_at")
    protect("usage_cost", APPEND_ONLY)


def downgrade() -> None:
    drop("usage_cost", "action_log", "approval", "draft", "tool_call", "agent_run")
