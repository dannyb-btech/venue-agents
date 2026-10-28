"""A migrated stamp database with two tenants, and the Platform API wired to fakes.

Tenant A has venues V1 and V2 and these users (Entra `oid` -> role):
- owner: tenant-wide owner (every venue)
- approver: approver on V1
- viewer: viewer on V1
- manager2: manager on V2 only
- disabled: approver on V1, but the user is disabled
- stranger: an active user with no roles
Tenant B has one venue and an approver. Rows are written with the admin login (setup only).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Generator, Mapping
from dataclasses import dataclass, field
from uuid import UUID

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import Engine, text

from va_core.secrets import SecretStore
from va_core.settings import Environment
from va_db.ids import uuid7
from va_db.rls import APP_ROLE, PROVISION_ROLE
from va_db.session import create_runtime_engine
from va_db.settings import MigrationSettings
from va_db.testing import TempDatabase, temp_database
from va_platform_api.api import VENUE_HEADER, create_app
from va_platform_api.auth import DevTokenValidator
from va_platform_api.events import InMemoryPublisher
from va_platform_api.oauth import STATE_KEY_SECRET, OAuthGrant

STAMP = "canary-local"
DEV_KEY = SecretStr("k" * 40)
TENANT_CLAIM = "va_tenant_id"
REDIRECT = "http://localhost:3000/connections/callback"

USERS_A = {
    "owner": (None, "owner"),
    "approver": ("v1", "approver"),
    "viewer": ("v1", "viewer"),
    "manager2": ("v2", "manager"),
    "disabled": ("v1", "approver"),
    "stranger": None,
}


@dataclass
class Tenant:
    tenant_id: UUID
    venues: dict[str, UUID]
    users: dict[str, UUID]


@dataclass
class World:
    db: TempDatabase
    admin: Engine
    a: Tenant
    b: Tenant
    validator: DevTokenValidator = field(
        default_factory=lambda: DevTokenValidator(DEV_KEY, Environment.TEST, TENANT_CLAIM)
    )

    def token(self, oid: str, tenant: Tenant | UUID | None = None) -> str:
        tenant_id = tenant.tenant_id if isinstance(tenant, Tenant) else tenant
        return self.validator.mint(subject=oid, tenant_id=tenant_id or self.a.tenant_id)

    def headers(
        self, oid: str, venue: UUID | None = None, tenant: Tenant | None = None
    ) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token(oid, tenant)}"}
        if venue is not None:
            headers[VENUE_HEADER] = str(venue)
        return headers

    def draft(
        self,
        tenant: Tenant,
        venue: UUID,
        payload: Mapping[str, object] | None = None,
        draft_type: str = "gmail.reply",
    ) -> UUID:
        draft_id = uuid7()
        with self.admin.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO draft (tenant_id, id, venue_id, type, payload, reasoning_summary) "
                    "VALUES (:t, :id, :v, :type, CAST(:p AS jsonb), 'because')"
                ),
                {
                    "t": tenant.tenant_id,
                    "id": draft_id,
                    "v": venue,
                    "type": draft_type,
                    "p": json.dumps(dict(payload or {"to": "guest@example.test"})),
                },
            )
        return draft_id

    def scalar(self, sql: str, **params: object) -> object:
        with self.admin.connect() as conn:
            return conn.execute(text(sql), params).scalar()


def _seed_tenant(admin: Engine, users: dict[str, tuple[str | None, str] | None]) -> Tenant:
    tenant_id = uuid7()
    venues = {"v1": uuid7(), "v2": uuid7()}
    ids: dict[str, UUID] = {}
    with admin.begin() as conn:
        conn.execute(
            text("INSERT INTO tenant (tenant_id, name) VALUES (:t, 'Group')"), {"t": tenant_id}
        )
        for name, venue_id in venues.items():
            conn.execute(
                text("INSERT INTO venue (tenant_id, id, name) VALUES (:t, :v, :n)"),
                {"t": tenant_id, "v": venue_id, "n": f"Venue {name}"},
            )
        for oid, grant in users.items():
            user_id = uuid7()
            ids[oid] = user_id
            conn.execute(
                text(
                    "INSERT INTO app_user (tenant_id, id, external_subject, email, status) "
                    "VALUES (:t, :u, :s, :e, :st)"
                ),
                {
                    "t": tenant_id,
                    "u": user_id,
                    "s": oid,
                    "e": f"{oid}@example.test",
                    "st": "disabled" if oid == "disabled" else "active",
                },
            )
            if grant is not None:
                venue_key, role = grant
                conn.execute(
                    text(
                        "INSERT INTO role_assignment (tenant_id, id, user_id, venue_id, role) "
                        "VALUES (:t, :id, :u, :v, :r)"
                    ),
                    {
                        "t": tenant_id,
                        "id": uuid7(),
                        "u": user_id,
                        "v": venues[venue_key] if venue_key else None,
                        "r": role,
                    },
                )
    return Tenant(tenant_id=tenant_id, venues=venues, users=ids)


@pytest.fixture(scope="session")
def world() -> Generator[World]:
    url = MigrationSettings.load().database_admin_url
    if url is None:
        pytest.fail("DATABASE_ADMIN_URL must be set (see .env.example); these tests need Postgres")
    with temp_database(url.get_secret_value(), logins=(APP_ROLE, PROVISION_ROLE)) as db:
        admin = db.admin_engine()
        try:
            a = _seed_tenant(admin, USERS_A)
            b = _seed_tenant(admin, {"approver": ("v1", "approver")})
            yield World(db=db, admin=admin, a=a, b=b)
        finally:
            admin.dispose()


class MemoryStore(SecretStore):
    def __init__(self) -> None:
        self.values: dict[str, SecretStr] = {STATE_KEY_SECRET: SecretStr("s" * 40)}

    async def get(self, name: str) -> SecretStr:
        return self.values[name]

    async def set(self, name: str, value: SecretStr) -> None:
        self.values[name] = value

    async def aclose(self) -> None:
        return None


class FakeProvider:
    system = "fakemail"

    def __init__(self) -> None:
        self.exchanged: list[tuple[str, str, str]] = []

    def authorize_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return f"https://auth.example.test/authorize?state={state}&challenge={code_challenge}"

    async def exchange(self, *, code: str, code_verifier: str, redirect_uri: str) -> OAuthGrant:
        self.exchanged.append((code, code_verifier, redirect_uri))
        return OAuthGrant(refresh_token=SecretStr(f"refresh-{code}"), scopes=("mail.read",))


class NoteEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str
    body: str


@dataclass
class Api:
    client: httpx.AsyncClient
    publisher: InMemoryPublisher
    store: MemoryStore
    provider: FakeProvider


@pytest.fixture
async def api(world: World) -> AsyncGenerator[Api]:
    publisher, store, provider = InMemoryPublisher(), MemoryStore(), FakeProvider()
    app = create_app(
        engine=create_runtime_engine(world.db.app_url),
        validator=world.validator,
        publisher=publisher,
        secret_store=store,
        stamp_id=STAMP,
        oauth_redirect_uri=REDIRECT,
        oauth_providers={"fakemail": provider},
        editable_types={"test.note": NoteEdit},
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://platform.test"
        ) as client,
    ):
        yield Api(client=client, publisher=publisher, store=store, provider=provider)
