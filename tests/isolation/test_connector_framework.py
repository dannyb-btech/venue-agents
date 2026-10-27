"""Connector framework on Postgres: drafts, connections, secrets and refresh locks per tenant."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from uuid import UUID

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr
from sqlalchemy import create_engine, pool, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.isolation.conftest import TENANT_A, TENANT_B
from tests.isolation.support import SET_TENANT, Seeded, Tenant, as_tenant, seed
from va_core.credentials import ConnectionInfo, TenantCredentials, TokenCache, TokenGrant
from va_core.errors import AuthorizationError, ExternalServiceError, TenantContextError
from va_core.mcp.drafts import NewDraft
from va_core.mcp.envelope import ENVELOPE_HEADER, envelope_key_secret_name, issue_envelope
from va_core.mcp.server import build_connector_server, connector_app
from va_core.mcp.settings import ConnectorSettings
from va_core.mcp.testing import MemorySecretStore, make_keys, sample_registry
from va_core.secrets import TenantSecrets, venue_secret_name
from va_core.tenancy import TenantContext
from va_db.connector_deps import build_connector_deps
from va_db.connectors import PgRefreshLock, SqlConnectionStore, SqlDraftWriter
from va_db.ids import uuid7
from va_db.models import Base, Draft
from va_db.rls import APP_ROLE, PROVISION_ROLE
from va_db.session import create_runtime_engine, create_sessionmaker
from va_db.testing import temp_database

SYSTEM = "sample"
NOTE = NewDraft(type="sample.note", payload={"title": "t"})


def _ctx(tenant: Tenant, venue_id: UUID | None = None) -> TenantContext:
    return TenantContext(
        tenant_id=tenant.tenant_id,
        venue_id=venue_id or tenant.refs["venue"],
        stamp_id="canary-local",
    )


def _insert(seeded: Seeded, tenant: Tenant, table: str, **values: object) -> UUID:
    row_id = uuid7()
    with seeded.app.begin() as conn:
        conn.execute(SET_TENANT, {"t": str(tenant.tenant_id)})
        conn.execute(
            Base.metadata.tables[table]
            .insert()
            .values(tenant_id=tenant.tenant_id, id=row_id, **values)
        )
    return row_id


def _connect(seeded: Seeded, ctx: TenantContext, tenant: Tenant, system: str, ref: str) -> None:
    _insert(
        seeded, tenant, "connection",
        venue_id=ctx.venue_id, system=system, status="active", secret_ref=ref,
    )  # fmt: skip


class World:
    """A's first venue, a second venue of A, and B, each connected to its own account."""

    def __init__(self, seeded: Seeded) -> None:
        self.a, self.b = _ctx(seeded.a), _ctx(seeded.b)
        second = _insert(seeded, seeded.a, "venue", name="second venue")
        self.a2 = _ctx(seeded.a, second)
        self.secrets: dict[str, str] = {}
        for ctx, tenant in ((self.a, seeded.a), (self.a2, seeded.a), (self.b, seeded.b)):
            ref = venue_secret_name(ctx, SYSTEM, "refresh-token")
            _connect(seeded, ctx, tenant, SYSTEM, ref)
            self.secrets[ref] = f"rt-{ctx.venue_id}"
        # A forged row: A's venue pointing at B's secret.
        _connect(
            seeded, self.a, seeded.a, "forged", venue_secret_name(self.b, SYSTEM, "refresh-token")
        )
        self.done_run = _insert(
            seeded, seeded.a, "agent_run",
            venue_id=self.a.venue_id, agent="hello", prompt_version="v1",
            model="claude-sonnet", status="succeeded",
        )  # fmt: skip


@pytest.fixture(scope="module")
def seeded(admin_url: str) -> Generator[Seeded]:
    """Own database: this module adds venues, connections and runs other modules don't expect."""
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


@pytest.fixture(scope="module")
def world(seeded: Seeded) -> Generator[World]:
    yield World(seeded)


@pytest.fixture
async def engine(seeded: Seeded) -> AsyncGenerator[AsyncEngine]:
    engine = create_runtime_engine(seeded.db.app_url.render_as_string(hide_password=False))
    yield engine
    await engine.dispose()


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(engine)


def _draft_tenants(seeded: Seeded, draft_id: UUID) -> dict[str, bool]:
    seen: dict[str, bool] = {}
    for name, tenant in (("a", seeded.a), ("b", seeded.b)):
        with as_tenant(seeded.app, tenant.tenant_id) as conn:
            found = conn.execute(select(Draft.id).where(Draft.id == draft_id)).first()
            seen[name] = found is not None
    return seen


async def test_draft_writer_writes_for_the_context_tenant_only(
    seeded: Seeded, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    draft_id = await SqlDraftWriter(sessionmaker).create(
        _ctx(seeded.a), seeded.a.refs["agent_run"], NOTE
    )
    assert _draft_tenants(seeded, draft_id) == {"a": True, "b": False}
    with seeded.admin.connect() as conn:
        row = conn.execute(select(Draft).where(Draft.id == draft_id)).one()
    assert (row.tenant_id, row.venue_id, row.run_id, row.status) == (
        seeded.a.tenant_id,
        seeded.a.refs["venue"],
        seeded.a.refs["agent_run"],
        "pending",
    )


async def test_draft_writer_refuses_another_tenants_run(
    seeded: Seeded, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(AuthorizationError, match="venue"):
        await SqlDraftWriter(sessionmaker).create(_ctx(seeded.a), seeded.b.refs["agent_run"], NOTE)


async def test_draft_writer_refuses_another_venues_run(
    seeded: Seeded, world: World, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A run of A's first venue can't create drafts for A's second venue."""
    with pytest.raises(AuthorizationError, match="venue"):
        await SqlDraftWriter(sessionmaker).create(world.a2, seeded.a.refs["agent_run"], NOTE)


async def test_draft_writer_refuses_a_finished_run(
    world: World, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(AuthorizationError, match="finished"):
        await SqlDraftWriter(sessionmaker).create(world.a, world.done_run, NOTE)


async def test_connection_store_sees_only_the_context_venue(
    seeded: Seeded, world: World, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    store = SqlConnectionStore(sessionmaker)
    for ctx in (world.a, world.a2, world.b):
        info = await store.get(ctx, SYSTEM)
        assert info is not None
        assert info.secret_ref == venue_secret_name(ctx, SYSTEM, "refresh-token")
    # Tenant A with B's venue (a context that should never exist) finds nothing under RLS.
    assert await store.get(_ctx(seeded.a, seeded.b.refs["venue"]), SYSTEM) is None


def _creds(
    world: World,
    ctx: TenantContext,
    store: MemorySecretStore,
    sessionmaker: async_sessionmaker[AsyncSession],
    tokens: TokenCache,
    system: str = SYSTEM,
) -> TenantCredentials:
    connections = SqlConnectionStore(sessionmaker)

    async def lookup() -> ConnectionInfo | None:
        return await connections.get(ctx, system)

    return TenantCredentials(
        ctx,
        system,
        lookup=lookup,
        secrets=TenantSecrets(store, ctx),
        tokens=tokens,
        refresh_lock=PgRefreshLock(sessionmaker),
    )


async def test_two_venues_of_one_tenant_get_their_own_tokens(
    world: World, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    store, tokens = MemorySecretStore(world.secrets), TokenCache()

    async def refresh(_info: ConnectionInfo, current: SecretStr) -> TokenGrant:
        return TokenGrant(
            access_token=SecretStr(f"at-{current.get_secret_value()}"), expires_at=10**12
        )

    seen = {
        ctx.venue_id: (
            await _creds(world, ctx, store, sessionmaker, tokens).access_token(refresh)
        ).get_secret_value()
        for ctx in (world.a, world.a2, world.b)
    }
    assert seen == {ctx.venue_id: f"at-rt-{ctx.venue_id}" for ctx in (world.a, world.a2, world.b)}


async def test_a_connection_naming_another_tenants_secret_is_refused(
    world: World, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """M6: even a forged connection row can't make tenant A read tenant B's secret."""
    store = MemorySecretStore(world.secrets)
    creds = _creds(world, world.a, store, sessionmaker, TokenCache(), system="forged")
    with pytest.raises(TenantContextError):
        await creds.secret()
    assert store.reads == []


async def test_refresh_lock_serialises_one_credential(
    seeded: Seeded, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    lock = PgRefreshLock(sessionmaker, wait=0.3)
    a, b = _ctx(seeded.a), _ctx(seeded.b)
    held, release = asyncio.Event(), asyncio.Event()

    async def holder() -> None:
        async with lock.hold(a, "cred-1"):
            held.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await held.wait()
    try:
        # Same tenant and credential: refused after the bounded wait (no pile-up on the pool).
        with pytest.raises(ExternalServiceError, match="timed out"):
            async with lock.hold(a, "cred-1"):
                pass
        # Another tenant (even with the same ref text) or another credential: not blocked.
        async with asyncio.timeout(5):
            async with lock.hold(b, "cred-1"):
                pass
            async with lock.hold(a, "cred-2"):
                pass
    finally:
        release.set()
        await task
    async with asyncio.timeout(5), lock.hold(a, "cred-1"):
        pass


async def test_sample_connector_end_to_end(
    seeded: Seeded,
    world: World,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start-up via `build_connector_deps`; an envelope for A drives drafts into A's rows only."""
    keys = make_keys("k1")
    signing_key = keys.key("k1")
    assert signing_key is not None
    store = MemorySecretStore(
        {**world.secrets, envelope_key_secret_name("k1"): signing_key.get_secret_value()}
    )
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CONNECTOR_SYSTEM", SYSTEM)
    monkeypatch.setenv("RUN_ENVELOPE_KIDS", '["k1"]')
    settings = ConnectorSettings.load()
    registry = sample_registry(
        httpx.MockTransport(
            lambda r: httpx.Response(200, json={"title": "t", "body": r.headers["authorization"]})
        )
    )
    deps = await build_connector_deps(settings, store, sessionmaker, registry=registry)
    server = build_connector_server(registry, deps)
    app = connector_app(server, allowed_hosts=["testserver"])
    envelope = issue_envelope(
        keys,
        world.a,
        system=SYSTEM,
        run_id=seeded.a.refs["agent_run"],
        agent="hello",
        tools=["sample_get_note", "sample_draft_note"],
    )
    async with server.session_manager.run():
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
            headers={ENVELOPE_HEADER: envelope},
        )
        async with (
            http,
            Client(streamable_http_client("http://testserver/mcp", http_client=http)) as client,
        ):
            read = await client.call_tool("sample_get_note", {"note_id": "n1"})
            drafted = await client.call_tool("sample_draft_note", {"title": "Hi", "body": "Body"})
    # The read tool authenticated with venue A's own credential, nobody else's.
    assert not read.is_error and read.structured_content is not None
    assert read.structured_content["body"] == f"Bearer rt-{world.a.venue_id}"
    assert not drafted.is_error and drafted.structured_content is not None
    draft_id = UUID(str(drafted.structured_content["draft_id"]))
    assert _draft_tenants(seeded, draft_id) == {"a": True, "b": False}
