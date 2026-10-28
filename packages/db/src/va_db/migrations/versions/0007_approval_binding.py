"""Bind approvals to the content approved; drafts are immutable once decided (P0-03 review H2).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-28

- `approval.payload_sha256`: SHA-256 (hex) of the canonical JSON of what will be executed (the
  edited payload if the approver edited it, else the draft's). The action executor recomputes it
  and refuses on a mismatch (`va_core.payloads`). Required for approvals; a rejection records the
  hash of what was shown.
- `va_draft_guard()` on `draft`: identity columns never change; payload, reasoning and sources
  change only while `pending`; status moves only pending -> approved/rejected/superseded and
  approved -> executed/failed; only pending drafts can be deleted.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRAFT_GUARD_FN = """
CREATE FUNCTION public.va_draft_guard() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'pending' THEN
            RAISE EXCEPTION 'a decided draft cannot be deleted' USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.venue_id IS DISTINCT FROM OLD.venue_id OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.type IS DISTINCT FROM OLD.type OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION 'draft identity columns are immutable' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status <> 'pending' AND (
        NEW.payload IS DISTINCT FROM OLD.payload
        OR NEW.reasoning_summary IS DISTINCT FROM OLD.reasoning_summary
        OR NEW.sources IS DISTINCT FROM OLD.sources
    ) THEN
        RAISE EXCEPTION 'a decided draft cannot change' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        (OLD.status = 'pending' AND NEW.status IN ('approved', 'rejected', 'superseded'))
        OR (OLD.status = 'approved' AND NEW.status IN ('executed', 'failed'))
    ) THEN
        RAISE EXCEPTION 'draft status transition not allowed' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$
"""


def upgrade() -> None:
    op.add_column("approval", sa.Column("payload_sha256", sa.Text(), nullable=True))
    # op.f(): the name is final; otherwise the metadata naming convention prefixes it again.
    op.create_check_constraint(
        op.f("ck_approval_payload_sha256"),
        "approval",
        "payload_sha256 ~ '^[0-9a-f]{64}$' "
        "AND (decision <> 'approved' OR payload_sha256 IS NOT NULL)",
    )
    op.execute(DRAFT_GUARD_FN)
    op.execute(
        "CREATE TRIGGER va_draft_guard BEFORE UPDATE OR DELETE ON draft "
        "FOR EACH ROW EXECUTE FUNCTION public.va_draft_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER va_draft_guard ON draft")
    op.execute("DROP FUNCTION public.va_draft_guard()")
    op.drop_constraint(op.f("ck_approval_payload_sha256"), "approval", type_="check")
    op.drop_column("approval", "payload_sha256")
