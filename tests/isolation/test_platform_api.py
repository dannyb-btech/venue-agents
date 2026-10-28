"""Platform API across tenants: a user of tenant A can't reach tenant B's records (ADR-0019).

Uses the seeded tenants (one row per table each; tenant A's user is an approver on its venue).
Every attempt names tenant B's real IDs directly, so only the token's tenant and RLS stand in the
way.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from tests.isolation.support import Seeded
from va_core.payloads import payload_sha256
from va_core.secrets import EnvFileSecretStore
from va_core.settings import Environment
from va_db.session import create_runtime_engine
from va_platform_api.api import VENUE_HEADER, create_app
from va_platform_api.auth import DevTokenValidator
from va_platform_api.events import InMemoryPublisher

VALIDATOR = DevTokenValidator(SecretStr("i" * 40), Environment.TEST, "va_tenant_id")


@pytest.fixture
async def client(seeded: Seeded) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app(
        engine=create_runtime_engine(seeded.db.app_url),
        validator=VALIDATOR,
        publisher=InMemoryPublisher(),
        secret_store=EnvFileSecretStore(path="nonexistent.env"),
        stamp_id="canary-local",
        oauth_redirect_uri="http://localhost:3000/connections/callback",
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield client


def _auth(seeded: Seeded, tenant_id: object, venue: object) -> dict[str, str]:
    with seeded.admin.connect() as conn:
        subject = conn.execute(
            text("SELECT external_subject FROM app_user WHERE id = :u"),
            {"u": seeded.a.refs["app_user"]},
        ).scalar_one()
    token = VALIDATOR.mint(subject=subject, tenant_id=tenant_id)  # type: ignore[arg-type]
    return {"Authorization": f"Bearer {token}", VENUE_HEADER: str(venue)}


async def test_own_tenant_works(client: httpx.AsyncClient, seeded: Seeded) -> None:
    headers = _auth(seeded, seeded.a.tenant_id, seeded.a.refs["venue"])
    response = await client.get(f"/drafts/{seeded.a.refs['draft']}", headers=headers)
    assert response.status_code == 200


async def test_tenant_b_venue_is_refused(client: httpx.AsyncClient, seeded: Seeded) -> None:
    headers = _auth(seeded, seeded.a.tenant_id, seeded.b.refs["venue"])
    for method, path in (
        ("GET", "/drafts"),
        ("GET", f"/drafts/{seeded.b.refs['draft']}"),
        ("GET", "/connections"),
        ("GET", f"/runs/{seeded.b.refs['agent_run']}"),
    ):
        response = await client.request(method, path, headers=headers)
        assert response.status_code == 403, path


async def test_tenant_b_ids_through_tenant_a_venue_are_not_found(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    headers = _auth(seeded, seeded.a.tenant_id, seeded.a.refs["venue"])
    draft_b, run_b = seeded.b.refs["draft"], seeded.b.refs["agent_run"]
    shown = payload_sha256(seeded.b.rows["draft"]["payload"])
    assert (await client.get(f"/drafts/{draft_b}", headers=headers)).status_code == 404
    assert (await client.get(f"/runs/{run_b}", headers=headers)).status_code == 404
    approve = await client.post(
        f"/drafts/{draft_b}/approve", json={"shown_sha256": shown}, headers=headers
    )
    assert approve.status_code == 404
    reject = await client.post(
        f"/drafts/{draft_b}/reject", json={"shown_sha256": shown, "reason": "x"}, headers=headers
    )
    assert reject.status_code == 404
    publish = await client.post(f"/drafts/{draft_b}/approval/publish", headers=headers)
    assert publish.status_code == 404
    listed = (await client.get("/drafts", headers=headers)).json()
    assert str(draft_b) not in {d["id"] for d in listed}
    with seeded.admin.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM draft WHERE id = :d"), {"d": draft_b}
        ).scalar_one()
    assert status == "pending"


async def test_claiming_tenant_b_with_a_users_subject_is_refused(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    # A token naming tenant B but carrying tenant A's user: no such user in B.
    headers = _auth(seeded, seeded.b.tenant_id, seeded.b.refs["venue"])
    response = await client.get("/drafts", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "not_a_user"
