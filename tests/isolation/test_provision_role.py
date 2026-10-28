"""`va_provision`: the control plane's stamp role creates tenants and venues, and nothing else.

It is not an RLS bypass: every insert and read is checked against the session's tenant (ADR-0017).
"""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.isolation.conftest import TENANT_A, TENANT_B
from tests.isolation.support import Seeded, as_tenant
from va_core.errors import ConfigurationError
from va_db.ids import uuid7
from va_db.rls import APP_ROLE, PROVISION_ROLE
from va_db.session import assert_rls_enforced

# Changing this is a security decision (ADR-0017, ADR-0019 for users): say why in the PR.
EXPECTED_PRIVILEGES = {
    "tenant": {"SELECT", "INSERT"},
    "venue": {"SELECT", "INSERT"},
    "app_user": {"SELECT", "INSERT"},
    "role_assignment": {"SELECT", "INSERT"},
}

_INSERT_TENANT = text("INSERT INTO tenant (tenant_id, name) VALUES (:t, 'New tenant')")
_INSERT_VENUE = text("INSERT INTO venue (tenant_id, id, name) VALUES (:t, :v, 'New venue')")


def test_provision_role_has_exactly_the_expected_privileges(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = :r"
            ),
            {"r": PROVISION_ROLE},
        ).all()
        role = conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcanlogin, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = :r"
            ),
            {"r": PROVISION_ROLE},
        ).one()
        owned = conn.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE r.rolname = :r"
            ),
            {"r": PROVISION_ROLE},
        ).scalar_one()
    granted: dict[str, set[str]] = {}
    for row in rows:
        granted.setdefault(row.table_name, set()).add(row.privilege_type)
    assert granted == EXPECTED_PRIVILEGES
    assert not (role.rolsuper or role.rolbypassrls or role.rolcanlogin)
    assert not (role.rolcreaterole or role.rolcreatedb)
    assert owned == 0


def test_creates_a_tenant_and_venue_for_the_session_tenant(seeded: Seeded) -> None:
    new = uuid7()
    with as_tenant(seeded.provision, new) as conn:
        conn.execute(_INSERT_TENANT, {"t": new})
        conn.execute(_INSERT_VENUE, {"t": new, "v": uuid7()})
        tenants = conn.execute(text("SELECT tenant_id FROM tenant")).scalars().all()
        venues = conn.execute(text("SELECT count(*) FROM venue")).scalar_one()
    assert tenants == [new]
    assert venues == 1


def test_cannot_create_a_tenant_other_than_the_session_tenant(seeded: Seeded) -> None:
    with (
        as_tenant(seeded.provision, uuid7()) as conn,
        pytest.raises(DBAPIError, match="row-level security"),
    ):
        conn.execute(_INSERT_TENANT, {"t": uuid7()})


def test_cannot_add_a_venue_to_another_tenant(seeded: Seeded) -> None:
    with (
        as_tenant(seeded.provision, TENANT_A) as conn,
        pytest.raises(DBAPIError, match="row-level security"),
    ):
        conn.execute(_INSERT_VENUE, {"t": TENANT_B, "v": uuid7()})


def test_fails_closed_without_a_tenant(seeded: Seeded) -> None:
    with as_tenant(seeded.provision, None) as conn:
        assert conn.execute(text("SELECT count(*) FROM tenant")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM venue")).scalar_one() == 0
    with (
        as_tenant(seeded.provision, None) as conn,
        pytest.raises(DBAPIError, match="row-level security"),
    ):
        conn.execute(_INSERT_TENANT, {"t": uuid7()})


def test_sees_only_the_session_tenant(seeded: Seeded) -> None:
    with as_tenant(seeded.provision, TENANT_A) as conn:
        tenants = conn.execute(text("SELECT tenant_id FROM tenant")).scalars().all()
    assert tenants == [TENANT_A]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE tenant SET status = 'suspended'",
        "DELETE FROM tenant",
        "UPDATE venue SET name = 'x'",
        "DELETE FROM venue",
        "UPDATE app_user SET status = 'disabled'",
        "DELETE FROM app_user",
        "UPDATE role_assignment SET role = 'owner'",
        "DELETE FROM role_assignment",
        "SELECT count(*) FROM connection",
        "SELECT count(*) FROM draft",
        "SELECT count(*) FROM approval",
        "SELECT count(*) FROM oauth_state",
        "INSERT INTO agent_run (venue_id, agent, prompt_version, model) "
        "VALUES (gen_random_uuid(), 'x', 'v1', 'm')",
    ],
)
def test_cannot_touch_anything_else(seeded: Seeded, statement: str) -> None:
    with (
        as_tenant(seeded.provision, TENANT_A) as conn,
        pytest.raises(ProgrammingError, match="permission denied"),
    ):
        conn.execute(text(statement))


async def test_assert_rls_enforced_for_the_provision_role(seeded: Seeded) -> None:
    provision = create_async_engine(seeded.db.logins[PROVISION_ROLE])
    app = create_async_engine(seeded.db.logins[APP_ROLE])
    try:
        await assert_rls_enforced(provision, PROVISION_ROLE)
        with pytest.raises(ConfigurationError, match="not a member of va_provision"):
            await assert_rls_enforced(app, PROVISION_ROLE)
        with pytest.raises(ConfigurationError, match="not a member of va_app"):
            await assert_rls_enforced(provision)
    finally:
        await provision.dispose()
        await app.dispose()


def test_no_column_grants_and_nothing_granted_to_public(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        columns = conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.role_column_grants "
                "WHERE grantee = :r "
                "AND table_name NOT IN ('tenant', 'venue', 'app_user', 'role_assignment')"
            ),
            {"r": PROVISION_ROLE},
        ).all()
        public = conn.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'PUBLIC' AND table_schema = 'public'"
            )
        ).all()
    assert columns == []
    assert public == []


async def test_a_login_in_two_platform_roles_is_refused(seeded: Seeded) -> None:
    suffix = secrets.token_hex(6)
    login, password = f"va_test_both_{suffix}", secrets.token_hex(16)
    with seeded.admin.connect() as conn:
        conn.execute(
            text(f"CREATE ROLE {login} LOGIN PASSWORD '{password}' IN ROLE va_app, va_provision")
        )
        conn.commit()
    engine = create_async_engine(seeded.db.admin_url.set(username=login, password=password))
    try:
        with pytest.raises(ConfigurationError, match="also a member of va_app"):
            await assert_rls_enforced(engine, PROVISION_ROLE)
        with pytest.raises(ConfigurationError, match="also a member of va_provision"):
            await assert_rls_enforced(engine)
    finally:
        await engine.dispose()
        with seeded.admin.connect() as conn:
            conn.execute(text(f"DROP ROLE {login}"))
            conn.commit()


_INSERT_USER = text(
    "INSERT INTO app_user (tenant_id, id, external_subject) VALUES (:t, :u, :s) "
    "ON CONFLICT (tenant_id, external_subject) DO NOTHING"
)
_INSERT_ROLE = text(
    "INSERT INTO role_assignment (tenant_id, id, user_id, venue_id, role) "
    "VALUES (:t, gen_random_uuid(), :u, :v, 'owner')"
)


def test_adds_users_and_roles_for_the_session_tenant_only(seeded: Seeded) -> None:
    user = uuid7()
    with as_tenant(seeded.provision, TENANT_A) as conn:
        conn.execute(_INSERT_USER, {"t": TENANT_A, "u": user, "s": f"oid-{user}"})
        conn.execute(_INSERT_ROLE, {"t": TENANT_A, "u": user, "v": seeded.a.refs["venue"]})
        assert conn.execute(text("SELECT count(*) FROM role_assignment")).scalar_one() >= 1
    with (
        as_tenant(seeded.provision, TENANT_A) as conn,
        pytest.raises(DBAPIError, match="row-level security"),
    ):
        conn.execute(_INSERT_USER, {"t": TENANT_B, "u": uuid7(), "s": "oid-other"})
    with (
        as_tenant(seeded.provision, TENANT_A) as conn,
        pytest.raises(DBAPIError, match=r"row-level security|foreign key"),
    ):
        # Tenant B's user and venue are invisible, so the FK fails too.
        conn.execute(
            _INSERT_ROLE,
            {"t": TENANT_B, "u": seeded.b.refs["app_user"], "v": seeded.b.refs["venue"]},
        )
