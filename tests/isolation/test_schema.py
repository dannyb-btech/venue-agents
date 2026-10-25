"""Structural guarantees, read from the Postgres catalog so they hold for every table."""

from __future__ import annotations

from sqlalchemy import text

from tests.isolation.support import Seeded
from va_db.rls import APP_ROLE, POLICY_NAME

USER_SCHEMA = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
    "AND n.nspname NOT LIKE 'pg_temp%'"
)
_RW = {"SELECT", "INSERT", "UPDATE", "DELETE"}
_APPEND = {"SELECT", "INSERT"}
# What runtime code may do per table. Changing this is a security decision: say why in the PR.
EXPECTED_PRIVILEGES: dict[str, set[str]] = {
    "tenant": {"SELECT"},
    "venue": _RW,
    "app_user": _RW,
    "role_assignment": _RW,
    "connection": _RW,
    "agent_config": _RW,
    "agent_run": _RW,
    "tool_call": _APPEND,
    "draft": _RW,
    "approval": _APPEND,
    "action_log": {"SELECT", "INSERT", "UPDATE"},
    "usage_cost": _APPEND,
    "sync_cursor": _RW,
    "document": _RW,
    "chunk": _RW,
}

TABLES = text(
    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version'"
)


def test_every_table_has_forced_rls_and_the_tenant_policy(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        tables = conn.execute(TABLES).all()
        policies = {
            (row.tablename, row.policyname): row
            for row in conn.execute(
                text(
                    "SELECT tablename, policyname, cmd, permissive, roles, qual, with_check "
                    "FROM pg_policies WHERE schemaname = 'public'"
                )
            )
        }
    assert tables
    for table in tables:
        assert table.relrowsecurity, f"{table.relname}: RLS not enabled"
        assert table.relforcerowsecurity, f"{table.relname}: RLS not forced"
        policy = policies.get((table.relname, POLICY_NAME))
        assert policy is not None, f"{table.relname}: no {POLICY_NAME} policy"
        assert policy.cmd == "ALL"
        assert policy.permissive == "PERMISSIVE"
        assert policy.qual == "(tenant_id = va_current_tenant())"
        assert policy.with_check == "(tenant_id = va_current_tenant())"
        assert list(policy.roles) == ["public"]
    assert {name for name, _ in policies} == {t.relname for t in tables}
    assert all(policy == POLICY_NAME for _, policy in policies), "unexpected extra policies"


def test_user_schemas_hold_only_plain_tables(seeded: Seeded) -> None:
    """Views and materialized views run as their owner and would bypass RLS; other schemas escape
    the checks above. Allow a view only with security_invoker, and update this test with it."""
    with seeded.admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT n.nspname, c.relname, c.relkind FROM pg_class c "  # noqa: S608 (constant filter)
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                f"WHERE {USER_SCHEMA} AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
            )
        ).all()
    unexpected = [(r.nspname, r.relname, r.relkind) for r in rows if r.relkind != "r"]
    assert unexpected == []
    assert {r.nspname for r in rows} == {"public"}


def test_app_role_has_exactly_the_expected_table_privileges(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = :r"
            ),
            {"r": APP_ROLE},
        ).all()
        column_acls = conn.execute(
            text(
                "SELECT c.relname, a.attname FROM pg_attribute a "  # noqa: S608 (constant filter)
                "JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                f"WHERE {USER_SCHEMA} AND a.attacl IS NOT NULL"
            )
        ).all()
        default_acls = conn.execute(text("SELECT count(*) FROM pg_default_acl")).scalar_one()
    granted: dict[str, set[str]] = {}
    for row in rows:
        granted.setdefault(row.table_name, set()).add(row.privilege_type)
    assert granted == EXPECTED_PRIVILEGES
    assert column_acls == []
    assert default_acls == 0


def test_app_role_cannot_create_objects(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        row = conn.execute(
            text(
                "SELECT has_schema_privilege(:r, 'public', 'CREATE') AS schema_create, "
                "has_schema_privilege(:r, 'public', 'USAGE') AS schema_usage, "
                "has_database_privilege(:r, current_database(), 'CREATE') AS db_create"
            ),
            {"r": APP_ROLE},
        ).one()
    assert not row.schema_create
    assert row.schema_usage
    assert not row.db_create


def test_no_security_definer_functions(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        names = conn.execute(
            text(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "  # noqa: S608 (constant filter)
                f"WHERE {USER_SCHEMA} AND p.prosecdef"
            )
        ).all()
    assert names == []


def test_current_tenant_function_is_bound_and_fail_closed(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        fn = conn.execute(
            text(
                "SELECT p.provolatile, p.prosecdef, p.prosqlbody IS NOT NULL AS atomic, "
                "pg_get_functiondef(p.oid) AS body FROM pg_proc p "
                "WHERE p.oid = 'public.va_current_tenant()'::regprocedure"
            )
        ).one()
        unset = conn.execute(text("SELECT va_current_tenant()")).scalar_one()
    assert fn.provolatile == "s"
    assert not fn.prosecdef
    assert fn.atomic, "must be BEGIN ATOMIC (names bound at creation)"
    assert "current_setting('app.tenant_id'::text, true)" in fn.body
    assert unset is None


def test_every_primary_key_starts_with_tenant_id(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT conrelid::regclass::text AS tbl, "
                "(SELECT attname FROM pg_attribute "
                " WHERE attrelid = conrelid AND attnum = conkey[1]) AS first_col "
                "FROM pg_constraint WHERE contype = 'p' AND connamespace = 'public'::regnamespace "
                "AND conrelid::regclass::text <> 'alembic_version'"
            )
        ).all()
        table_count = len(conn.execute(TABLES).all())
    assert len(rows) == table_count
    for row in rows:
        assert row.first_col == "tenant_id", f"{row.tbl}: primary key must start with tenant_id"


def test_every_foreign_key_matches_tenant_id_to_tenant_id(seeded: Seeded) -> None:
    """With tenant_id = tenant_id in every FK, no row can reference another tenant's row."""
    with seeded.admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.conname, "
                "  array(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n, i) "
                "        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n "
                "        ORDER BY k.i) AS cols, "
                "  array(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(n, i) "
                "        JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.n "
                "        ORDER BY k.i) AS ref_cols "
                "FROM pg_constraint c WHERE c.contype = 'f' "
                "AND c.connamespace = 'public'::regnamespace"
            )
        ).all()
    assert rows
    for row in rows:
        pairs = list(zip(row.cols, row.ref_cols, strict=True))
        assert ("tenant_id", "tenant_id") in pairs, f"{row.conname}: FK must include tenant_id"


def test_every_index_starts_with_tenant_id(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT i.indexrelid::regclass::text AS idx, a.attname AS first_col "
                "FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "LEFT JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0] "
                "WHERE n.nspname = 'public' AND c.relname <> 'alembic_version'"
            )
        ).all()
    for row in rows:
        assert row.first_col == "tenant_id", f"{row.idx}: index must start with tenant_id"


def test_app_role_cannot_bypass_rls(seeded: Seeded) -> None:
    with seeded.admin.connect() as conn:
        role = conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcanlogin, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = :r"
            ),
            {"r": APP_ROLE},
        ).one()
        owned = conn.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE r.rolname = :r"
            ),
            {"r": APP_ROLE},
        ).scalar_one()
    assert not role.rolsuper
    assert not role.rolbypassrls
    assert not role.rolcanlogin
    assert not role.rolcreaterole
    assert not role.rolcreatedb
    assert owned == 0, "va_app must own no tables (owners can disable RLS)"


def test_runtime_login_inherits_no_bypass(seeded: Seeded) -> None:
    with seeded.app.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        is_member = conn.execute(
            text("SELECT pg_has_role(current_user, :r, 'MEMBER')"), {"r": APP_ROLE}
        ).scalar_one()
    assert not row.rolsuper
    assert not row.rolbypassrls
    assert is_member
