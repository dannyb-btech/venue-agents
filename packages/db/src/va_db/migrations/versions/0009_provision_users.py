"""va_provision adds venue users to the stamp (ADR-0019).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-28

Operators add users through the control plane (`POST /venues/{id}/users`), whose provisioner is
the one place a `TenantContext` is built from catalog state (ADR-0017). It gets SELECT and INSERT
on `app_user` and `role_assignment`, still under RLS. No UPDATE or DELETE: disabling users and
removing roles get their own grants when built.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from va_db.rls import PROVISION_ROLE

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT ON app_user, role_assignment TO {PROVISION_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON app_user, role_assignment FROM {PROVISION_ROLE}")
