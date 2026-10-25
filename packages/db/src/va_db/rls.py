"""Row-level security building blocks, shared by migrations and the isolation tests.

Every tenant table gets the same policy, created in the migration that creates the table:
rows are visible and writable only when `tenant_id` equals `va_current_tenant()`, which reads the
transaction-local `app.tenant_id` set by `va_db.session`. Unset means NULL, so nothing matches and
every write fails the WITH CHECK clause: queries fail closed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

APP_ROLE = "va_app"
"""Group role for runtime logins: no LOGIN, no BYPASSRLS, owns nothing."""

POLICY_NAME = "tenant_isolation"
TENANT_SETTING = "app.tenant_id"

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_PRIVILEGES = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})

CREATE_APP_ROLE = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END
$$
"""

# BEGIN ATOMIC binds names when the function is created (no search_path or pg_temp lookups when
# policies call it), and everything is schema-qualified. Every policy depends on this function.
CREATE_CURRENT_TENANT_FN = f"""
CREATE FUNCTION public.va_current_tenant() RETURNS pg_catalog.uuid
LANGUAGE sql STABLE PARALLEL SAFE
BEGIN ATOMIC
    SELECT NULLIF(pg_catalog.current_setting('{TENANT_SETTING}', true), '')
        ::pg_catalog.uuid;
END
"""

DROP_CURRENT_TENANT_FN = "DROP FUNCTION va_current_tenant()"


def _ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"not a safe SQL identifier: {name!r}")
    return name


def tenant_table_sql(table: str, privileges: Iterable[str]) -> list[str]:
    """Statements that put `table` under tenant RLS and grant `privileges` to the app role.

    Append-only tables (audit trail) are granted SELECT and INSERT only.
    """
    table = _ident(table)
    privs = sorted(set(privileges))
    if not privs or not set(privs) <= _PRIVILEGES:
        raise ValueError(f"privileges must be a non-empty subset of {sorted(_PRIVILEGES)}")
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {POLICY_NAME} ON {table} "
        "USING (tenant_id = va_current_tenant()) "
        "WITH CHECK (tenant_id = va_current_tenant())",
        f"GRANT {', '.join(privs)} ON {table} TO {APP_ROLE}",
    ]


def revoke_table_sql(table: str) -> str:
    """Revoke the app role's grants; policies are dropped with the table."""
    return f"REVOKE ALL ON {_ident(table)} FROM {APP_ROLE}"
