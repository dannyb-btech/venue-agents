"""Creating a tenant and venue in a stamp database, as `va_provision` (ADR-0017).

The control plane connects to each stamp with the connection details from the catalog's `stamp`
row and a password from a provider: an Entra token for its managed identity in Azure, a local
secret (`stamp-<id>-provision-db-password`) in development. Every write goes through
`tenant_session`, so RLS checks the rows belong to the tenant being provisioned.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import URL
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from va_core.secrets import SecretStore
from va_core.settings import Environment
from va_core.tenancy import TenantContext
from va_db.catalog.models import SslMode, Stamp
from va_db.credentials import ENTRA_POOL_RECYCLE_SECONDS, SecretPassword, check_deployed_tls
from va_db.models import Tenant, Venue
from va_db.rls import PROVISION_ROLE
from va_db.session import (
    PasswordProvider,
    assert_rls_enforced,
    create_runtime_engine,
    create_sessionmaker,
    tenant_session,
)

CONTROL_PLANE_USER = "control-plane"


@dataclass(frozen=True)
class StampConnection:
    """Where a stamp's database is and who the control plane logs in as. Not secret."""

    stamp_id: str
    host: str
    port: int
    database: str
    user: str
    sslmode: SslMode

    @classmethod
    def from_stamp(cls, stamp: Stamp) -> StampConnection:
        return cls(
            stamp_id=stamp.id,
            host=stamp.db_host,
            port=stamp.db_port,
            database=stamp.db_name,
            user=stamp.db_provision_user,
            sslmode=SslMode(stamp.db_sslmode),
        )

    def url(self) -> URL:
        query = {"sslmode": self.sslmode.value}
        if self.sslmode == SslMode.VERIFY_FULL:
            query["sslrootcert"] = "system"  # libpq 16+: the OS trust store (Azure's public CAs)
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            host=self.host,
            port=self.port,
            database=self.database,
            query=query,
        )


@dataclass(frozen=True)
class ProvisionRequest:
    tenant_id: UUID
    venue_id: UUID
    tenant_name: str | None
    """Set when the tenant is new; None adds a venue to a tenant that already exists."""
    venue_name: str
    timezone: str


class StampProvisioner(Protocol):
    async def provision(self, stamp: StampConnection, request: ProvisionRequest) -> None:
        """Create the tenant (if new) and venue. Idempotent: rows that exist are left alone."""
        ...

    async def aclose(self) -> None: ...


def local_password(store: SecretStore) -> Callable[[StampConnection], PasswordProvider]:
    """Development: each stamp's password is the secret `stamp-<id>-provision-db-password`."""

    def for_stamp(stamp: StampConnection) -> PasswordProvider:
        return SecretPassword(store, f"stamp-{stamp.stamp_id}-provision-db-password")

    return for_stamp


def entra_password(provider: PasswordProvider) -> Callable[[StampConnection], PasswordProvider]:
    """Azure: one managed identity (an Entra principal on every stamp's server) for all stamps."""

    def for_stamp(_stamp: StampConnection) -> PasswordProvider:
        return provider

    return for_stamp


class DatabaseStampProvisioner:
    """Keeps one small pool per stamp, checked on first use to be a `va_provision` login."""

    def __init__(
        self,
        password_for: Callable[[StampConnection], PasswordProvider],
        environment: Environment,
    ) -> None:
        self._password_for = password_for
        self._environment = environment
        self._pools: dict[
            str, tuple[StampConnection, AsyncEngine, async_sessionmaker[AsyncSession]]
        ]
        self._pools = {}
        self._lock = asyncio.Lock()

    async def _sessions(self, stamp: StampConnection) -> async_sessionmaker[AsyncSession]:
        async with self._lock:
            cached = self._pools.get(stamp.stamp_id)
            if cached is not None and cached[0] == stamp:
                return cached[2]
            if cached is not None:  # connection details changed: drop the old pool
                await cached[1].dispose()
                del self._pools[stamp.stamp_id]
            # Outside local: Azure Postgres hosts with verify-full only (the token is the password).
            check_deployed_tls(stamp.url(), self._environment)
            engine = create_runtime_engine(
                stamp.url(),
                password=self._password_for(stamp),
                pool_size=2,
                max_overflow=2,
                pool_recycle=ENTRA_POOL_RECYCLE_SECONDS,
            )
            try:
                await assert_rls_enforced(engine, PROVISION_ROLE)
            except BaseException:
                await engine.dispose()
                raise
            sessions = create_sessionmaker(engine)
            self._pools[stamp.stamp_id] = (stamp, engine, sessions)
            return sessions

    async def provision(self, stamp: StampConnection, request: ProvisionRequest) -> None:
        sessions = await self._sessions(stamp)
        ctx = TenantContext(
            tenant_id=request.tenant_id,
            venue_id=request.venue_id,
            stamp_id=stamp.stamp_id,
            user_id=CONTROL_PLANE_USER,
        )
        async with tenant_session(sessions, ctx) as session:
            if request.tenant_name is not None:
                await session.execute(
                    insert(Tenant)
                    .values(tenant_id=request.tenant_id, name=request.tenant_name)
                    .on_conflict_do_nothing(index_elements=["tenant_id"])
                )
            await session.execute(
                insert(Venue)
                .values(
                    tenant_id=request.tenant_id,
                    id=request.venue_id,
                    name=request.venue_name,
                    timezone=request.timezone,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "id"])
            )

    async def aclose(self) -> None:
        async with self._lock:
            for _, engine, _ in self._pools.values():
                await engine.dispose()
            self._pools.clear()
