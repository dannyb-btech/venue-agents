"""Catalog: stamp, tenant_directory, venue_directory, entitlement, onboarding_job; va_catalog role.

Revision ID: c0001
Revises:
Create Date: 2026-10-27

No RLS: the catalog is cross-tenant by design (ADR-0017). Only `va_catalog` members reach it.
`stamp` is SELECT-only for the role (operators register stamps with the admin login); nothing is
DELETE-able (offboarding is a status). The downgrade leaves the cluster-wide role in place.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from va_db.migrations.helpers import check_in, timestamp
from va_db.rls import CATALOG_ROLE, create_group_role_sql

revision: str = "c0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Inlined, not imported from the models: an applied migration must never change.
STAMP_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
_DIRECTORY_STATUS = ("provisioning", "active", "suspended", "offboarding", "offboarded")
_TABLES = ("onboarding_job", "entitlement", "venue_directory", "tenant_directory", "stamp")


def upgrade() -> None:
    op.execute(create_group_role_sql(CATALOG_ROLE))
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {CATALOG_ROLE}")

    op.create_table(
        "stamp",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("ring", sa.Text(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("db_host", sa.Text(), nullable=False),
        sa.Column("db_port", sa.Integer(), nullable=False, server_default="5432"),
        sa.Column("db_name", sa.Text(), nullable=False),
        sa.Column("db_provision_user", sa.Text(), nullable=False),
        sa.Column("db_sslmode", sa.Text(), nullable=False, server_default="verify-full"),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_stamp"),
        sa.CheckConstraint(f"id ~ '{STAMP_ID_PATTERN}'", name="ck_stamp_id"),
        sa.CheckConstraint("capacity > 0", name="ck_stamp_capacity"),
        check_in("stamp", "ring", ("canary", "general")),
        check_in("stamp", "status", ("active", "draining", "closed")),
        check_in("stamp", "db_sslmode", ("disable", "require", "verify-ca", "verify-full")),
    )

    op.create_table(
        "tenant_directory",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("stamp_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="provisioning"),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant_directory"),
        sa.ForeignKeyConstraint(
            ["stamp_id"], ["stamp.id"], name="fk_tenant_directory_stamp_id_stamp"
        ),
        sa.UniqueConstraint("tenant_id", "stamp_id", name="uq_tenant_directory_tenant_id_stamp_id"),
        check_in("tenant_directory", "status", _DIRECTORY_STATUS),
    )
    op.create_index("ix_tenant_directory_stamp_id", "tenant_directory", ["stamp_id"])

    op.create_table(
        "venue_directory",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("stamp_id", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="provisioning"),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "venue_id", name="pk_venue_directory"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "stamp_id"],
            ["tenant_directory.tenant_id", "tenant_directory.stamp_id"],
            name="fk_venue_directory_tenant_id_stamp_id_tenant_directory",
            # A later move-tenant tool updates tenant and venues in one transaction.
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.UniqueConstraint("venue_id", name="uq_venue_directory_venue_id"),
        check_in("venue_directory", "status", _DIRECTORY_STATUS),
    )
    op.create_index("ix_venue_directory_stamp_id_status", "venue_directory", ["stamp_id", "status"])

    op.create_table(
        "entitlement",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("entitlement", sa.Text(), nullable=False),
        timestamp("granted_at"),
        timestamp("revoked_at", nullable=True, default_now=False),
        sa.PrimaryKeyConstraint("tenant_id", "venue_id", "entitlement", name="pk_entitlement"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "venue_id"],
            ["venue_directory.tenant_id", "venue_directory.venue_id"],
            name="fk_entitlement_tenant_id_venue_id_venue_directory",
        ),
        sa.CheckConstraint(
            "entitlement ~ '^[a-z][a-z0-9_]{0,62}$'", name="ck_entitlement_entitlement"
        ),
    )

    op.create_table(
        "onboarding_job",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_name", sa.Text(), nullable=True),
        sa.Column("venue_name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_onboarding_job"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "venue_id"],
            ["venue_directory.tenant_id", "venue_directory.venue_id"],
            name="fk_onboarding_job_tenant_id_venue_id_venue_directory",
        ),
        check_in("onboarding_job", "status", ("pending", "running", "succeeded", "failed")),
        sa.UniqueConstraint("idempotency_key", name="uq_onboarding_job_idempotency_key"),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9_-]{1,128}$'", name="ck_onboarding_job_idempotency_key"
        ),
    )
    op.create_index(
        "ix_onboarding_job_tenant_id_venue_id", "onboarding_job", ["tenant_id", "venue_id"]
    )

    op.execute(f"GRANT SELECT ON stamp TO {CATALOG_ROLE}")
    for table in _TABLES[:-1]:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {CATALOG_ROLE}")


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {CATALOG_ROLE}")
        op.drop_table(table)
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {CATALOG_ROLE}")
