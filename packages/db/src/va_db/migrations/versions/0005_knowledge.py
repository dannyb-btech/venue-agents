"""Knowledge: document and chunk (pgvector).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25

`chunk.embedding` has no dimension and no HNSW index yet: the embedding model is not chosen
(open-questions.md). The migration that fixes the dimension adds the index, with an ADR.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

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

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document",
        tenant_id(),
        id_(),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        timestamp("synced_at"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("blob_ref", sa.Text(), nullable=True),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_document"),
        fk("document", "venue_id", "venue"),
        unique("document", "tenant_id", "source_system", "source_id"),
    )
    protect("document", READ_WRITE)

    op.create_table(
        "chunk",
        tenant_id(),
        id_(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(), nullable=True),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_chunk"),
        fk("chunk", "document_id", "document", ondelete="CASCADE"),
        unique("chunk", "tenant_id", "document_id", "ordinal"),
    )
    protect("chunk", READ_WRITE)


def downgrade() -> None:
    drop("chunk", "document")
