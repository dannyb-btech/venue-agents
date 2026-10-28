"""OAuth connection scaffolding with a fake provider (real providers arrive in P0-07)."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from va_core.secrets import venue_secret_name
from va_core.tenancy import TenantContext

from .conftest import REDIRECT, STAMP, Api, World

pytestmark = pytest.mark.db


async def _start(api: Api, world: World, oid: str = "owner", venue: str = "v1") -> str:
    response = await api.client.post(
        "/connections/fakemail/oauth/start", headers=world.headers(oid, world.a.venues[venue])
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorize_url"]).query)
    return query["state"][0]


async def _callback(
    api: Api, world: World, state: str, oid: str = "owner", venue: str = "v1"
) -> int:
    response = await api.client.post(
        "/connections/fakemail/oauth/callback",
        json={"code": "the-code", "state": state},
        headers=world.headers(oid, world.a.venues[venue]),
    )
    return response.status_code


async def test_connect_stores_the_refresh_token_for_the_venue(api: Api, world: World) -> None:
    v2 = world.a.venues["v2"]
    response = await api.client.post(
        "/connections/fakemail/oauth/start", headers=world.headers("manager2", v2)
    )
    url = urlsplit(response.json()["authorize_url"])
    query = parse_qs(url.query)
    state, challenge = query["state"][0], query["challenge"][0]

    # Only the state's hash is stored, never the state or a verifier.
    stored = world.scalar("SELECT state_sha256 FROM oauth_state WHERE venue_id = :v", v=v2)
    assert stored == hashlib.sha256(state.encode()).hexdigest()

    done = await api.client.post(
        "/connections/fakemail/oauth/callback",
        json={"code": "abc", "state": state},
        headers=world.headers("manager2", v2),
    )
    assert done.status_code == 204, done.text

    (code, verifier, redirect) = api.provider.exchanged[-1]
    assert (code, redirect) == ("abc", REDIRECT)
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert digest.decode() == challenge

    ctx = TenantContext(tenant_id=world.a.tenant_id, venue_id=v2, stamp_id=STAMP)
    name = venue_secret_name(ctx, "fakemail", "refresh-token")
    assert api.store.values[name].get_secret_value() == "refresh-abc"
    listed = (await api.client.get("/connections", headers=world.headers("manager2", v2))).json()
    assert listed == [
        {
            "system": "fakemail",
            "status": "active",
            "scopes": ["mail.read"],
            "updated_at": listed[0]["updated_at"],
        }
    ]
    assert (
        world.scalar(
            "SELECT secret_ref FROM connection WHERE venue_id = :v AND system = 'fakemail'", v=v2
        )
        == name
    )


async def test_state_is_single_use(api: Api, world: World) -> None:
    state = await _start(api, world)
    assert await _callback(api, world, state) == 204
    assert await _callback(api, world, state) == 422


async def test_state_is_bound_to_user_and_venue(api: Api, world: World) -> None:
    state = await _start(api, world, oid="owner", venue="v1")
    # Another manager-level user, or the same user on another venue, can't complete it.
    assert await _callback(api, world, state, oid="owner", venue="v2") == 422
    assert await _callback(api, world, state, oid="manager2", venue="v2") == 422
    assert await _callback(api, world, state) == 204


async def test_another_user_on_the_same_venue_cannot_complete(api: Api, world: World) -> None:
    state = await _start(api, world, oid="owner", venue="v2")
    assert await _callback(api, world, state, oid="manager2", venue="v2") == 422
    assert await _callback(api, world, state, oid="owner", venue="v2") == 204


async def test_expired_state_is_refused(api: Api, world: World) -> None:
    state = await _start(api, world)
    with world.admin.begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text(
                "UPDATE oauth_state SET expires_at = now() - interval '1 minute' "
                "WHERE state_sha256 = :h"
            ),
            {"h": hashlib.sha256(state.encode()).hexdigest()},
        )
    assert await _callback(api, world, state) == 422
    assert api.provider.exchanged == []


async def test_unknown_or_invalid_system(api: Api, world: World) -> None:
    headers = world.headers("owner", world.a.venues["v1"])
    unknown = await api.client.post("/connections/gmail/oauth/start", headers=headers)
    assert unknown.status_code == 404
    invalid = await api.client.post("/connections/Bad-System/oauth/start", headers=headers)
    assert invalid.status_code == 422


@pytest.mark.parametrize("oid", ["approver", "viewer"])
async def test_only_owners_and_managers_connect(api: Api, world: World, oid: str) -> None:
    response = await api.client.post(
        "/connections/fakemail/oauth/start", headers=world.headers(oid, world.a.venues["v1"])
    )
    assert response.status_code == 403
