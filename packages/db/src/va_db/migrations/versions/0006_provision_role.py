"""The va_provision role: the control plane creates tenants and venues in the stamp (ADR-0017).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-27

`va_provision` gets SELECT and INSERT on `tenant` and `venue` only. RLS still applies (the policies
are for every role), so the control plane sets the new tenant with `tenant_session` before it
inserts. No UPDATE or DELETE: suspend and offboard get their own reviewed grants when they're built.
Like 0001, the downgrade leaves the cluster-wide role in place.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from va_db.rls import PROVISION_ROLE, create_group_role_sql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(create_group_role_sql(PROVISION_ROLE))
    op.execute(f"GRANT USAGE ON SCHEMA public TO {PROVISION_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON tenant, venue TO {PROVISION_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON tenant, venue FROM {PROVISION_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {PROVISION_ROLE}")
