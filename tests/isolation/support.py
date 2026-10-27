"""Shared helpers for the isolation tests (fixtures live in conftest.py)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from tests.isolation.factories import FACTORIES, Refs, Row, row_id
from va_db.models import Base
from va_db.testing import TempDatabase

SET_TENANT = text("SELECT set_config('app.tenant_id', :t, true)")


@dataclass(frozen=True)
class Tenant:
    tenant_id: UUID
    refs: Refs
    """table -> id of this tenant's seeded row."""
    rows: dict[str, Row]


@dataclass(frozen=True)
class Seeded:
    db: TempDatabase
    admin: Engine
    app: Engine
    provision: Engine
    """A `va_provision` member: the control plane's login in a stamp."""
    a: Tenant
    b: Tenant


@contextmanager
def as_tenant(engine: Engine, tenant_id: UUID | None) -> Iterator[Connection]:
    """A connection in a transaction that is rolled back, with `app.tenant_id` set (or not)."""
    with engine.connect() as conn, conn.begin() as tx:
        if tenant_id is not None:
            conn.execute(SET_TENANT, {"t": str(tenant_id)})
        try:
            yield conn
        finally:
            tx.rollback()


def seed(provision: Engine, app: Engine, tenant_id: UUID) -> Tenant:
    """Insert one row per table for `tenant_id`.

    The `tenant` row is created as `va_provision` (the control plane's job; the app role can't).
    Everything else is inserted as the app role. All inserts go through RLS.
    """
    refs: Refs = {}
    rows: dict[str, Row] = {}
    tenant_row = FACTORIES["tenant"](tenant_id, refs)
    with provision.begin() as conn:
        conn.execute(SET_TENANT, {"t": str(tenant_id)})
        conn.execute(Base.metadata.tables["tenant"].insert().values(**tenant_row))
    refs["tenant"], rows["tenant"] = tenant_id, tenant_row
    with app.begin() as conn:
        conn.execute(SET_TENANT, {"t": str(tenant_id)})
        for table, factory in FACTORIES.items():
            if table == "tenant":
                continue
            row = factory(tenant_id, refs)
            conn.execute(Base.metadata.tables[table].insert().values(**row))
            refs[table] = row_id(table, row)
            rows[table] = row
    return Tenant(tenant_id=tenant_id, refs=refs, rows=rows)
