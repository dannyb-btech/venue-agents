"""Migrations against a real Postgres: clean round trip, and models matching the migrated schema."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from va_core.errors import ConfigurationError
from va_db.models import Base
from va_db.settings import MigrationSettings
from va_db.testing import migrate, temp_database

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = MigrationSettings.load().database_admin_url
    if url is None:
        pytest.fail("DATABASE_ADMIN_URL must be set (see .env.example); db tests need Postgres")
    return url.get_secret_value()


def test_temp_database_refuses_non_local_servers() -> None:
    with (
        pytest.raises(ConfigurationError, match="local servers only"),
        temp_database("postgresql+psycopg://app:x@pg-canary.postgres.database.azure.com/stamp"),
    ):
        pass


def test_upgrade_downgrade_upgrade(admin_url: str) -> None:
    with temp_database(admin_url, revision=None) as db:
        engine = db.admin_engine()
        try:
            migrate(db.admin_url, "head")
            assert set(inspect(engine).get_table_names()) >= set(Base.metadata.tables)
            migrate(db.admin_url, "base", down=True)
            assert inspect(engine).get_table_names() == ["alembic_version"]
            migrate(db.admin_url, "head")
            assert set(inspect(engine).get_table_names()) >= set(Base.metadata.tables)
        finally:
            engine.dispose()


def test_each_step_downgrades_cleanly(admin_url: str) -> None:
    with temp_database(admin_url, revision=None) as db:
        for revision in (
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
            "0006",
            "0007",
            "0008",
            "0009",
            "0010",
        ):
            migrate(db.admin_url, revision)
            migrate(db.admin_url, "-1", down=True)
            migrate(db.admin_url, revision)


def test_models_match_migrations(admin_url: str) -> None:
    with temp_database(admin_url) as db:
        engine = db.admin_engine()
        try:
            with engine.connect() as conn:
                context = MigrationContext.configure(conn, opts={"compare_type": True})
                diff = compare_metadata(context, Base.metadata)
        finally:
            engine.dispose()
    assert diff == [], "models and migrations disagree; write a migration"
