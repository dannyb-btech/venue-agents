"""Every test under tests/isolation is a cross-tenant isolation test.

They run against a real Postgres (docker compose locally, a service container in CI) in a fresh,
migrated database. They fail, never skip, when the database isn't configured or reachable.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Generator
from uuid import UUID

import pytest

from tests.isolation.support import Seeded, seed
from va_db.settings import MigrationSettings
from va_db.testing import temp_database

TENANT_A = UUID("0193a000-0000-7000-8000-00000000000a")
TENANT_B = UUID("0193b000-0000-7000-8000-00000000000b")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/isolation" in item.path.as_posix():
            item.add_marker(pytest.mark.isolation)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg's async driver can't use Windows' default Proactor loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


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
    with temp_database(admin_url) as db:
        admin, app = db.admin_engine(), db.app_engine()
        try:
            a, b = seed(admin, app, TENANT_A), seed(admin, app, TENANT_B)
            yield Seeded(db=db, admin=admin, app=app, a=a, b=b)
        finally:
            admin.dispose()
            app.dispose()
