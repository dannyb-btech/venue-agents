"""Catalog migrations on a real Postgres: round trip, models match, the catalog role's grants."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from va_db.catalog.models import CatalogBase
from va_db.rls import CATALOG_ROLE
from va_db.settings import MigrationSettings
from va_db.testing import CATALOG_MIGRATIONS, migrate, temp_catalog

pytestmark = pytest.mark.db

_RW = {"SELECT", "INSERT", "UPDATE"}
# What the control plane may do in the catalog. Changing this is a security decision (ADR-0017).
EXPECTED_PRIVILEGES = {
    "stamp": {"SELECT"},
    "tenant_directory": _RW,
    "venue_directory": _RW,
    "entitlement": _RW,
    "onboarding_job": _RW,
}


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = MigrationSettings.load().database_admin_url
    if url is None:
        pytest.fail("DATABASE_ADMIN_URL must be set (see .env.example); db tests need Postgres")
    return url.get_secret_value()


def test_upgrade_downgrade_upgrade(admin_url: str) -> None:
    with temp_catalog(admin_url, revision=None) as db:
        engine = db.admin_engine()
        try:
            migrate(db.admin_url, "head", migrations=CATALOG_MIGRATIONS)
            assert set(inspect(engine).get_table_names()) >= set(CatalogBase.metadata.tables)
            migrate(db.admin_url, "base", down=True, migrations=CATALOG_MIGRATIONS)
            assert inspect(engine).get_table_names() == ["alembic_version"]
            migrate(db.admin_url, "head", migrations=CATALOG_MIGRATIONS)
        finally:
            engine.dispose()


def test_models_match_migrations(admin_url: str) -> None:
    with temp_catalog(admin_url) as db:
        engine = db.admin_engine()
        try:
            with engine.connect() as conn:
                context = MigrationContext.configure(conn, opts={"compare_type": True})
                diff = compare_metadata(context, CatalogBase.metadata)
        finally:
            engine.dispose()
    assert diff == [], "catalog models and migrations disagree; write a migration"


def test_catalog_role_has_exactly_the_expected_privileges(admin_url: str) -> None:
    with temp_catalog(admin_url) as db:
        engine = db.admin_engine()
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name, privilege_type "
                        "FROM information_schema.role_table_grants WHERE grantee = :r"
                    ),
                    {"r": CATALOG_ROLE},
                ).all()
                role = conn.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles "
                        "WHERE rolname = :r"
                    ),
                    {"r": CATALOG_ROLE},
                ).one()
        finally:
            engine.dispose()
    granted: dict[str, set[str]] = {}
    for row in rows:
        granted.setdefault(row.table_name, set()).add(row.privilege_type)
    assert granted == EXPECTED_PRIVILEGES
    assert not role.rolsuper
    assert not role.rolbypassrls
    assert not role.rolcanlogin


def test_catalog_login_cannot_register_stamps_or_delete(admin_url: str) -> None:
    with temp_catalog(admin_url) as db:
        app = db.app_engine()
        try:
            with app.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(
                    text(
                        "INSERT INTO stamp (id, region, ring, capacity, db_host, db_name, "
                        "db_provision_user) VALUES ('x', 'r', 'general', 1, 'h', 'd', 'u')"
                    )
                )
            with app.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("DELETE FROM venue_directory"))
        finally:
            app.dispose()


def test_venue_must_be_on_its_tenants_stamp(admin_url: str) -> None:
    with temp_catalog(admin_url) as db:
        admin = db.admin_engine()
        try:
            with admin.begin() as conn:
                for stamp in ("one", "two"):
                    conn.execute(
                        text(
                            "INSERT INTO stamp (id, region, ring, capacity, db_host, db_name, "
                            "db_provision_user) VALUES (:s, 'australiaeast', 'general', 10, "
                            "'h', 'd', 'u')"
                        ),
                        {"s": stamp},
                    )
                conn.execute(
                    text(
                        "INSERT INTO tenant_directory (tenant_id, stamp_id) "
                        "VALUES ('0193a000-0000-7000-8000-00000000000a', 'one')"
                    )
                )
            with admin.begin() as conn, pytest.raises(IntegrityError, match="foreign key"):
                conn.execute(
                    text(
                        "INSERT INTO venue_directory (tenant_id, venue_id, stamp_id, region, plan) "
                        "VALUES ('0193a000-0000-7000-8000-00000000000a', "
                        "'0193a000-0000-7000-8000-0000000000a1', 'two', 'australiaeast', 'pilot')"
                    )
                )
        finally:
            admin.dispose()


def test_stamp_ids_are_restricted(admin_url: str) -> None:
    with temp_catalog(admin_url) as db:
        admin = db.admin_engine()
        try:
            with admin.begin() as conn, pytest.raises(IntegrityError, match="ck_stamp_id"):
                conn.execute(
                    text(
                        "INSERT INTO stamp (id, region, ring, capacity, db_host, db_name, "
                        "db_provision_user) VALUES ('Bad_ID', 'r', 'general', 1, 'h', 'd', 'u')"
                    )
                )
        finally:
            admin.dispose()
