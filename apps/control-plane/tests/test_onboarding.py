"""Onboarding and routing end to end: one catalog and two stamp databases on local Postgres.

Two stamps on purpose (P0-04 acceptance): every test checks rows land on the chosen stamp and
nowhere else, so nothing can quietly assume a single stamp database.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from contextlib import ExitStack
from dataclasses import dataclass
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import URL, Engine, create_engine, pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from va_control_plane.__main__ import register_stamp
from va_control_plane.api import create_app
from va_control_plane.auth import DevTokenAuthenticator
from va_control_plane.provisioning import (
    DatabaseStampProvisioner,
    ProvisionRequest,
    StampConnection,
    StampProvisioner,
    UserGrant,
)
from va_core.errors import ConfigurationError
from va_core.http import RetryPolicy
from va_core.settings import Environment
from va_db.catalog.models import SslMode
from va_db.rls import APP_ROLE, PROVISION_ROLE
from va_db.session import PasswordProvider, create_runtime_engine
from va_db.settings import MigrationSettings
from va_db.testing import TempDatabase, temp_catalog, temp_database

pytestmark = pytest.mark.db

TOKEN = "d" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
AE = "australiaeast"
NO_WAIT = RetryPolicy(max_attempts=3, base_delay=0, max_delay=0)


@dataclass
class World:
    catalog: TempDatabase
    stamps: dict[str, TempDatabase]
    admin_url: str

    def register(
        self, stamp_id: str, *, ring: str = "general", capacity: int = 10, **kw: object
    ) -> None:
        db = self.stamps[stamp_id]
        login = db.logins[PROVISION_ROLE]
        register_stamp(
            self.catalog.admin_url.render_as_string(hide_password=False),
            {
                "id": stamp_id,
                "region": AE,
                "ring": ring,
                "capacity": capacity,
                "db_host": login.host,
                "db_port": login.port or 5432,
                "db_name": login.database,
                "db_provision_user": login.username,
                "db_sslmode": "disable",
                **kw,
            },
        )

    def password_for(self, stamp: StampConnection) -> PasswordProvider:
        password = str(self.stamps[stamp.stamp_id].logins[PROVISION_ROLE].password)

        async def provider() -> SecretStr:
            return SecretStr(password)

        return provider

    def stamp_rows(self, stamp_id: str, table: str) -> list[UUID]:
        column = "tenant_id" if table == "tenant" else "id"
        with _admin(self.stamps[stamp_id]) as engine, engine.connect() as conn:
            return list(conn.execute(text(f"SELECT {column} FROM {table}")).scalars())  # noqa: S608

    def catalog_row(self, sql: str, **params: object) -> dict[str, object]:
        with _admin(self.catalog) as engine, engine.connect() as conn:
            return dict(conn.execute(text(sql), params).mappings().one())


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = MigrationSettings.load().database_admin_url
    if url is None:
        pytest.fail("DATABASE_ADMIN_URL must be set (see .env.example); db tests need Postgres")
    return url.get_secret_value()


@pytest.fixture
def world(admin_url: str) -> Generator[World]:
    with ExitStack() as stack:
        catalog = stack.enter_context(temp_catalog(admin_url))
        stamps = {
            name: stack.enter_context(temp_database(admin_url, logins=(APP_ROLE, PROVISION_ROLE)))
            for name in ("stamp-a", "stamp-b")
        }
        yield World(catalog=catalog, stamps=stamps, admin_url=admin_url)


class Flaky:
    """Fails the first `failures` provisions, then delegates."""

    def __init__(self, inner: StampProvisioner, failures: int) -> None:
        self.inner = inner
        self.failures = failures

    async def provision(self, stamp: StampConnection, request: ProvisionRequest) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("stamp unreachable")
        await self.inner.provision(stamp, request)

    async def add_user(self, stamp: StampConnection, grant: UserGrant) -> UUID:
        return await self.inner.add_user(stamp, grant)

    async def aclose(self) -> None:
        await self.inner.aclose()


def _admin(db: TempDatabase) -> _EngineCtx:
    return _EngineCtx(create_engine(db.admin_url, poolclass=pool.NullPool))


class _EngineCtx:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def __enter__(self) -> Engine:
        return self.engine

    def __exit__(self, *_args: object) -> None:
        self.engine.dispose()


async def _client(world: World, failures: int = 0) -> AsyncGenerator[httpx.AsyncClient]:
    provisioner: StampProvisioner = DatabaseStampProvisioner(world.password_for, Environment.TEST)
    if failures:
        provisioner = Flaky(provisioner, failures)
    app = create_app(
        catalog_engine=create_runtime_engine(world.catalog.app_url),
        provisioner=provisioner,
        authenticator=DevTokenAuthenticator(SecretStr(TOKEN), Environment.TEST),
        provision_retry=NO_WAIT,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://cp", headers=AUTH) as c:
            yield c


@pytest.fixture
async def client(world: World) -> AsyncGenerator[httpx.AsyncClient]:
    world.register("stamp-a")
    world.register("stamp-b")
    async for c in _client(world):
        yield c


def _connection(stamp_id: str, login: URL) -> StampConnection:
    return StampConnection(
        stamp_id=stamp_id,
        host=str(login.host),
        port=login.port or 5432,
        database=str(login.database),
        user=str(login.username),
        sslmode=SslMode.DISABLE,
    )


REQUEST = ProvisionRequest(
    tenant_id=UUID("0193a000-0000-7000-8000-00000000000a"),
    venue_id=UUID("0193a000-0000-7000-8000-0000000000a1"),
    tenant_name="X",
    venue_name="X",
    timezone="Australia/Sydney",
)


def _new(name: str = "OR Group", **kw: object) -> dict[str, object]:
    return {"tenant_name": name, "venue_name": f"{name} venue", "region": AE, "plan": "pilot", **kw}


def _stamps(placed: list[dict[str, object]]) -> list[str]:
    return sorted(str(item["stamp_id"]) for item in placed)


async def test_onboarding_creates_catalog_and_stamp_records(
    world: World, client: httpx.AsyncClient
) -> None:
    response = await client.post(
        "/venues", json=_new(entitlements=["events_manager", "wine_buyer"])
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    tenant_id, venue_id, stamp_id = (
        UUID(body["tenant_id"]),
        UUID(body["venue_id"]),
        body["stamp_id"],
    )
    other = next(s for s in world.stamps if s != stamp_id)

    assert world.stamp_rows(stamp_id, "tenant") == [tenant_id]
    assert world.stamp_rows(stamp_id, "venue") == [venue_id]
    assert world.stamp_rows(other, "tenant") == []
    assert world.stamp_rows(other, "venue") == []

    venue = world.catalog_row(
        "SELECT v.status, v.stamp_id, t.status AS tenant_status FROM venue_directory v "
        "JOIN tenant_directory t USING (tenant_id) WHERE v.venue_id = :v",
        v=venue_id,
    )
    assert venue == {"status": "active", "stamp_id": stamp_id, "tenant_status": "active"}
    job = world.catalog_row(
        "SELECT status, attempts, error_code FROM onboarding_job WHERE venue_id = :v", v=venue_id
    )
    assert job == {"status": "succeeded", "attempts": 1, "error_code": None}

    entitlements = await client.get(f"/venues/{venue_id}/entitlements")
    assert entitlements.json() == {
        "venue_id": str(venue_id),
        "entitlements": ["events_manager", "wine_buyer"],
    }


async def test_routing_returns_each_venues_stamp(client: httpx.AsyncClient) -> None:
    placed = [(await client.post("/venues", json=_new(f"T{i}"))).json() for i in range(4)]
    assert _stamps(placed) == ["stamp-a", "stamp-a", "stamp-b", "stamp-b"]
    for venue in placed:
        route = await client.get("/route", params={"venue_id": venue["venue_id"]})
        assert route.status_code == 200
        assert route.json() == {
            "venue_id": venue["venue_id"],
            "stamp_id": venue["stamp_id"],
            "region": AE,
            "status": "active",
        }
    unknown = await client.get(
        "/route", params={"venue_id": "0193a000-0000-7000-8000-000000000000"}
    )
    assert unknown.status_code == 404


async def test_a_tenants_venues_stay_on_its_stamp(world: World, client: httpx.AsyncClient) -> None:
    first = (await client.post("/venues", json=_new())).json()
    home = first["stamp_id"]
    # Load the other stamp less, then add venues to the tenant: they still go home.
    for _ in range(2):
        added = await client.post(
            "/venues",
            json={
                "tenant_id": first["tenant_id"],
                "venue_name": "Second",
                "region": AE,
                "plan": "pilot",
            },
        )
        assert added.status_code == 201, added.text
        assert added.json()["stamp_id"] == home
        assert added.json()["tenant_id"] == first["tenant_id"]
    assert len(world.stamp_rows(home, "venue")) == 3
    assert world.stamp_rows(home, "tenant") == [UUID(first["tenant_id"])]


async def test_existing_tenant_cannot_be_split_across_stamps(client: httpx.AsyncClient) -> None:
    first = (await client.post("/venues", json=_new(stamp_id="stamp-a"))).json()
    response = await client.post(
        "/venues",
        json={
            "tenant_id": first["tenant_id"],
            "venue_name": "Elsewhere",
            "region": AE,
            "plan": "pilot",
            "stamp_id": "stamp-b",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "placement"


async def test_unknown_tenant_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/venues",
        json={
            "tenant_id": "0193a000-0000-7000-8000-00000000000f",
            "venue_name": "X",
            "region": AE,
            "plan": "pilot",
        },
    )
    assert response.status_code == 404


async def test_full_stamps_are_skipped_then_refused(world: World) -> None:
    world.register("stamp-a", capacity=1)
    world.register("stamp-b", capacity=1)
    async for client in _client(world):
        placed = [(await client.post("/venues", json=_new(f"T{i}"))).json() for i in range(2)]
        assert _stamps(placed) == ["stamp-a", "stamp-b"]
        refused = await client.post("/venues", json=_new("T3"))
        assert refused.status_code == 409
        stamps = (await client.get("/stamps")).json()
        assert [(s["id"], s["venues"], s["capacity"]) for s in stamps] == [
            ("stamp-a", 1, 1),
            ("stamp-b", 1, 1),
        ]


async def test_requested_stamp_and_ring(world: World) -> None:
    world.register("stamp-a", ring="canary")
    world.register("stamp-b")
    async for client in _client(world):
        general = (await client.post("/venues", json=_new("Client"))).json()
        assert general["stamp_id"] == "stamp-b"  # canary isn't in the general ring
        pilot = (await client.post("/venues", json=_new("OR", stamp_id="stamp-a"))).json()
        assert pilot["stamp_id"] == "stamp-a"


async def test_failed_provisioning_is_not_routable_and_retry_completes_it(world: World) -> None:
    world.register("stamp-a")
    world.register("stamp-b", status="closed")
    async for client in _client(world, failures=NO_WAIT.max_attempts):
        response = await client.post("/venues", json=_new())
        assert response.status_code == 201
        failed = response.json()
        assert (failed["status"], failed["error_code"]) == ("failed", "stamp_unavailable")
        venue_id = failed["venue_id"]
        assert world.stamp_rows("stamp-a", "venue") == []
        assert (await client.get("/route", params={"venue_id": venue_id})).status_code == 404

        retried = await client.post(f"/venues/{venue_id}/onboarding/retry")
        assert retried.status_code == 200
        assert retried.json()["status"] == "succeeded"
        assert retried.json()["job_id"] == failed["job_id"]
        assert (await client.get("/route", params={"venue_id": venue_id})).json()[
            "stamp_id"
        ] == "stamp-a"

        again = await client.post(f"/venues/{venue_id}/onboarding/retry")
        assert again.json()["status"] == "succeeded"
        job = world.catalog_row(
            "SELECT attempts FROM onboarding_job WHERE venue_id = :v", v=UUID(venue_id)
        )
        assert job == {"attempts": 2}
        assert world.stamp_rows("stamp-a", "venue") == [UUID(venue_id)]


async def test_provisioning_twice_leaves_one_row(world: World) -> None:
    world.register("stamp-a")
    async for client in _client(world):
        placed = (await client.post("/venues", json=_new())).json()
    connection = _connection("stamp-a", world.stamps["stamp-a"].logins[PROVISION_ROLE])
    provisioner = DatabaseStampProvisioner(world.password_for, Environment.TEST)
    try:
        await provisioner.provision(
            connection,
            ProvisionRequest(
                tenant_id=UUID(placed["tenant_id"]),
                venue_id=UUID(placed["venue_id"]),
                tenant_name="Renamed",
                venue_name="Renamed",
                timezone="Australia/Perth",
            ),
        )
    finally:
        await provisioner.aclose()
    assert world.stamp_rows("stamp-a", "venue") == [UUID(placed["venue_id"])]


async def test_provisioner_refuses_a_login_that_is_not_va_provision(world: World) -> None:
    app_login = world.stamps["stamp-a"].logins[APP_ROLE]
    world.register("stamp-a", db_provision_user=app_login.username)
    password = str(app_login.password)

    async def provider() -> SecretStr:
        return SecretStr(password)

    provisioner = DatabaseStampProvisioner(lambda _s: provider, Environment.TEST)
    connection = _connection("stamp-a", app_login)
    try:
        with pytest.raises(ConfigurationError, match="not a member of va_provision"):
            await provisioner.provision(connection, REQUEST)
    finally:
        await provisioner.aclose()


async def test_app_refuses_to_start_with_an_admin_catalog_login(world: World) -> None:
    app = create_app(
        catalog_engine=create_async_engine(world.catalog.admin_url),
        provisioner=DatabaseStampProvisioner(world.password_for, Environment.TEST),
        authenticator=DevTokenAuthenticator(SecretStr(TOKEN), Environment.TEST),
    )
    with pytest.raises(ConfigurationError, match="bypasses row-level security"):
        async with app.router.lifespan_context(app):
            pass


async def test_provisioner_refuses_unverified_tls_outside_local(world: World) -> None:
    provisioner = DatabaseStampProvisioner(world.password_for, Environment.PROD)
    login = world.stamps["stamp-a"].logins[PROVISION_ROLE]
    with pytest.raises(ConfigurationError, match="outside local"):
        await provisioner.provision(_connection("stamp-a", login), REQUEST)


async def test_transient_failures_are_retried_within_one_run(world: World) -> None:
    world.register("stamp-a")
    async for client in _client(world, failures=NO_WAIT.max_attempts - 1):
        placed = (await client.post("/venues", json=_new())).json()
    assert placed["status"] == "succeeded"
    job = world.catalog_row(
        "SELECT attempts FROM onboarding_job WHERE venue_id = :v", v=UUID(placed["venue_id"])
    )
    assert job == {"attempts": 1}


async def test_idempotency_key_replays_the_first_job(
    world: World, client: httpx.AsyncClient
) -> None:
    first = await client.post("/venues", json=_new(), headers={"Idempotency-Key": "onboard-or-1"})
    replay = await client.post("/venues", json=_new(), headers={"Idempotency-Key": "onboard-or-1"})
    other = await client.post("/venues", json=_new(), headers={"Idempotency-Key": "onboard-or-2"})
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert other.json()["venue_id"] != first.json()["venue_id"]
    venues = world.stamp_rows("stamp-a", "venue") + world.stamp_rows("stamp-b", "venue")
    assert len(venues) == 2
    bad = await client.post("/venues", json=_new(), headers={"Idempotency-Key": "no spaces"})
    assert bad.status_code == 422


async def test_concurrent_onboardings_cannot_overfill_a_stamp(world: World) -> None:
    world.register("stamp-a", capacity=1)
    async for client in _client(world):
        responses = await asyncio.gather(
            *(client.post("/venues", json=_new(f"T{i}")) for i in range(4))
        )
    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409]
    assert len(world.stamp_rows("stamp-a", "venue")) == 1


async def test_revoked_entitlements_are_not_returned(
    world: World, client: httpx.AsyncClient
) -> None:
    placed = (
        await client.post("/venues", json=_new(entitlements=["events_manager", "wine_buyer"]))
    ).json()
    with _admin(world.catalog) as engine, engine.begin() as conn:
        conn.execute(
            text("UPDATE entitlement SET revoked_at = now() WHERE entitlement = 'wine_buyer'")
        )
    response = await client.get(f"/venues/{placed['venue_id']}/entitlements")
    assert response.json()["entitlements"] == ["events_manager"]


async def test_running_job_is_retried_only_once_stale(
    world: World, client: httpx.AsyncClient
) -> None:
    placed = (await client.post("/venues", json=_new())).json()
    venue_id = placed["venue_id"]

    def set_running(age: str) -> None:
        with _admin(world.catalog) as engine, engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE onboarding_job SET status = 'running', "
                    "updated_at = now() - CAST(:age AS interval) WHERE venue_id = :v"
                ),
                {"age": age, "v": UUID(venue_id)},
            )

    set_running("1 minute")
    busy = await client.post(f"/venues/{venue_id}/onboarding/retry")
    assert busy.status_code == 409
    assert busy.json()["code"] == "onboarding_conflict"
    set_running("10 minutes")
    recovered = await client.post(f"/venues/{venue_id}/onboarding/retry")
    assert recovered.json()["status"] == "succeeded"


# --- venue users (ADR-0019) ----------------------------------------------------------------------


def _stamp_query(
    world: World, stamp_id: str, sql: str, **params: object
) -> list[tuple[object, ...]]:
    with _admin(world.stamps[stamp_id]) as engine, engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]


async def test_add_user_creates_user_and_role_in_the_venues_stamp(
    world: World, client: httpx.AsyncClient
) -> None:
    placed = (await client.post("/venues", json=_new())).json()
    venue_id, stamp_id = placed["venue_id"], placed["stamp_id"]
    other = next(s for s in world.stamps if s != stamp_id)
    body = {"external_subject": "oid-123", "email": "gm@example.test", "role": "manager"}

    first = await client.post(f"/venues/{venue_id}/users", json=body)
    assert first.status_code == 201, first.text
    again = await client.post(
        f"/venues/{venue_id}/users", json={**body, "email": "new@example.test"}
    )
    assert again.json()["user_id"] == first.json()["user_id"]
    owner = await client.post(
        f"/venues/{venue_id}/users", json={**body, "role": "owner", "tenant_wide": True}
    )
    assert owner.status_code == 201

    users = _stamp_query(world, stamp_id, "SELECT tenant_id, external_subject, email FROM app_user")
    assert users == [(UUID(placed["tenant_id"]), "oid-123", "gm@example.test")]
    roles = _stamp_query(
        world, stamp_id, "SELECT venue_id, role FROM role_assignment ORDER BY role"
    )
    assert sorted(roles, key=str) == sorted([(UUID(venue_id), "manager"), (None, "owner")], key=str)
    assert _stamp_query(world, other, "SELECT id FROM app_user") == []


async def test_add_user_needs_an_active_venue(client: httpx.AsyncClient) -> None:
    body = {"external_subject": "oid-1", "role": "viewer"}
    unknown = await client.post("/venues/0193a000-0000-7000-8000-000000000000/users", json=body)
    assert unknown.status_code == 404
    bad = await client.post(
        "/venues/0193a000-0000-7000-8000-000000000000/users",
        json={**body, "tenant_id": "0193a000-0000-7000-8000-00000000000a"},
    )
    assert bad.status_code == 422
