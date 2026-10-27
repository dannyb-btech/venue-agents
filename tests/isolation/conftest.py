"""Every test under tests/isolation is a cross-tenant isolation test.

They run against a real Postgres (docker compose locally, a service container in CI) in a fresh,
migrated database. They fail, never skip, when the database isn't configured or reachable.
"""

from __future__ import annotations

from collections.abc import Generator
from uuid import UUID

import pytest
from sqlalchemy import create_engine, pool

from tests.isolation.support import Seeded, seed
from va_db.rls import APP_ROLE, PROVISION_ROLE
from va_db.settings import MigrationSettings
from va_db.testing import temp_database

TENANT_A = UUID("0193a000-0000-7000-8000-00000000000a")
TENANT_B = UUID("0193b000-0000-7000-8000-00000000000b")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/isolation" in item.path.as_posix():
            item.add_marker(pytest.mark.isolation)


@pytest.fixture(scope="session")
def admin_url() -> str:
    url = MigrationSettings.load().database_admin_url
    if url is None:
        pytest.fail(
            "DATABASE_ADMIN_URL must be set (see .env.example); isolation tests need Postgres"
        )
    return url.get_secret_value()


@pytest.fixture(scope="session")
def seeded(admin_url: str) -> Generator[Seeded]:
    with temp_database(admin_url, logins=(APP_ROLE, PROVISION_ROLE)) as db:
        admin, app = db.admin_engine(), db.app_engine()
        provision = create_engine(db.logins[PROVISION_ROLE], poolclass=pool.NullPool)
        try:
            a, b = seed(provision, app, TENANT_A), seed(provision, app, TENANT_B)
            yield Seeded(db=db, admin=admin, app=app, provision=provision, a=a, b=b)
        finally:
            admin.dispose()
            app.dispose()
            provision.dispose()
