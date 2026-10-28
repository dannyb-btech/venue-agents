"""A draft can only become `approved` with an approval row behind it (P0-05 security review M1).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-28

A deferred constraint trigger checks, at commit, that a draft moved to `approved` has an
`approval` with decision `approved` and a `payload_sha256`. Code that can UPDATE `draft` (today
every `va_app` member) can no longer mark a draft approved without recording who approved what.
The Platform API inserts the approval and updates the draft in one transaction, so it passes.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPROVAL_REQUIRED_FN = """
CREATE FUNCTION public.va_draft_approval_required() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.status = 'approved' AND NOT EXISTS (
        SELECT 1 FROM public.approval a
        WHERE a.tenant_id = NEW.tenant_id AND a.draft_id = NEW.id
          AND a.decision = 'approved' AND a.payload_sha256 IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'an approved draft needs an approval' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$
"""


def upgrade() -> None:
    op.execute(APPROVAL_REQUIRED_FN)
    op.execute(
        "CREATE CONSTRAINT TRIGGER va_draft_approval_required AFTER INSERT OR UPDATE OF status "
        "ON draft DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.va_draft_approval_required()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER va_draft_approval_required ON draft")
    op.execute("DROP FUNCTION public.va_draft_approval_required()")
