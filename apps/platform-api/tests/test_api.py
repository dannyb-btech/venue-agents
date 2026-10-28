"""Platform API behaviour on a real stamp database (P0-05 acceptance: a viewer can't approve)."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text

from va_core.payloads import payload_sha256
from va_db.ids import uuid7

from .conftest import STAMP, Api, World

pytestmark = pytest.mark.db

PAYLOAD = {"to": "guest@example.test", "body": "Thanks for your enquiry"}
SHOWN = payload_sha256(PAYLOAD)


# --- who you are ---------------------------------------------------------------------------------


async def test_me_lists_venues_with_roles(api: Api, world: World) -> None:
    owner = (await api.client.get("/me", headers=world.headers("owner"))).json()
    assert owner["user_id"] == str(world.a.users["owner"])
    assert {v["venue_id"]: v["roles"] for v in owner["venues"]} == {
        str(world.a.venues["v1"]): ["owner"],
        str(world.a.venues["v2"]): ["owner"],
    }
    approver = (await api.client.get("/me", headers=world.headers("approver"))).json()
    assert [(v["venue_id"], v["roles"]) for v in approver["venues"]] == [
        (str(world.a.venues["v1"]), ["approver"])
    ]
    stranger = (await api.client.get("/me", headers=world.headers("stranger"))).json()
    assert stranger["venues"] == []


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic abc"}],
)
async def test_missing_or_bad_tokens_are_401(api: Api, headers: dict[str, str]) -> None:
    response = await api.client.get("/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_token_signed_with_another_key_is_401(api: Api, world: World) -> None:
    from pydantic import SecretStr

    from va_core.settings import Environment
    from va_platform_api.auth import DevTokenValidator

    forged = DevTokenValidator(SecretStr("x" * 40), Environment.TEST, "va_tenant_id").mint(
        subject="owner", tenant_id=world.a.tenant_id
    )
    response = await api.client.get("/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_tenant_not_on_this_stamp_is_403(api: Api, world: World) -> None:
    response = await api.client.get(
        "/me", headers={"Authorization": f"Bearer {world.token('owner', uuid7())}"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "tenant_not_on_stamp"


async def test_user_of_another_tenant_is_not_a_user_here(api: Api, world: World) -> None:
    # Tenant A's owner oid presented with tenant B's ID: no such user in B.
    response = await api.client.get("/me", headers=world.headers("owner", tenant=world.b))
    assert response.status_code == 403
    assert response.json()["code"] == "not_a_user"


async def test_suspended_tenant_is_403(api: Api, world: World) -> None:
    tenant_id = uuid7()
    with world.admin.begin() as conn:
        conn.execute(
            text("INSERT INTO tenant (tenant_id, name, status) VALUES (:t, 'S', 'suspended')"),
            {"t": tenant_id},
        )
        conn.execute(
            text("INSERT INTO app_user (tenant_id, id, external_subject) VALUES (:t, :u, 'owner')"),
            {"t": tenant_id, "u": uuid7()},
        )
    response = await api.client.get(
        "/me", headers={"Authorization": f"Bearer {world.token('owner', tenant_id)}"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "tenant_suspended"


async def test_disabled_user_is_403(api: Api, world: World) -> None:
    response = await api.client.get("/me", headers=world.headers("disabled"))
    assert response.status_code == 403
    assert response.json()["code"] == "not_a_user"


# --- venue selection -----------------------------------------------------------------------------


async def test_venue_header_is_required(api: Api, world: World) -> None:
    response = await api.client.get("/drafts", headers=world.headers("owner"))
    assert response.status_code == 422


async def test_venue_without_a_role_is_403(api: Api, world: World) -> None:
    for oid, venue in (("approver", "v2"), ("manager2", "v1"), ("stranger", "v1")):
        response = await api.client.get(
            "/drafts", headers=world.headers(oid, world.a.venues[venue])
        )
        assert response.status_code == 403, oid


async def test_another_tenants_venue_is_403(api: Api, world: World) -> None:
    response = await api.client.get("/drafts", headers=world.headers("owner", world.b.venues["v1"]))
    assert response.status_code == 403


# --- drafts --------------------------------------------------------------------------------------


async def test_list_and_get_drafts_for_the_venue_only(api: Api, world: World) -> None:
    v1, v2 = world.a.venues["v1"], world.a.venues["v2"]
    mine = world.draft(world.a, v1, PAYLOAD)
    other_venue = world.draft(world.a, v2, PAYLOAD)
    headers = world.headers("viewer", v1)

    listed = (await api.client.get("/drafts?status=pending", headers=headers)).json()
    ids = {d["id"] for d in listed}
    assert str(mine) in ids
    assert str(other_venue) not in ids

    detail = (await api.client.get(f"/drafts/{mine}", headers=headers)).json()
    assert detail["payload"] == PAYLOAD
    assert detail["payload_sha256"] == SHOWN
    assert detail["editable"] is False
    assert (await api.client.get(f"/drafts/{other_venue}", headers=headers)).status_code == 404


@pytest.mark.parametrize("path", ["approve", "reject", "approval/publish"])
async def test_viewer_cannot_decide(api: Api, world: World, path: str) -> None:
    draft = world.draft(world.a, world.a.venues["v1"], PAYLOAD)
    body = {"shown_sha256": SHOWN, "reason": "no"} if path == "reject" else {"shown_sha256": SHOWN}
    response = await api.client.post(
        f"/drafts/{draft}/{path}",
        json=body if path != "approval/publish" else None,
        headers=world.headers("viewer", world.a.venues["v1"]),
    )
    assert response.status_code == 403
    assert world.scalar("SELECT status FROM draft WHERE id = :d", d=draft) == "pending"
    assert world.scalar("SELECT count(*) FROM approval WHERE draft_id = :d", d=draft) == 0
    assert api.publisher.sent == []


async def test_approver_approves_and_the_executor_is_told(api: Api, world: World) -> None:
    v1 = world.a.venues["v1"]
    draft = world.draft(world.a, v1, PAYLOAD)
    response = await api.client.post(
        f"/drafts/{draft}/approve",
        json={"shown_sha256": SHOWN},
        headers=world.headers("approver", v1),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["event_published"] is True
    assert body["payload_sha256"] == SHOWN

    assert world.scalar("SELECT status FROM draft WHERE id = :d", d=draft) == "approved"
    stored = world.scalar(
        "SELECT payload_sha256 FROM approval WHERE id = :a", a=UUID(body["approval_id"])
    )
    assert stored == SHOWN
    user = world.scalar("SELECT user_id FROM approval WHERE draft_id = :d", d=draft)
    assert user == world.a.users["approver"]
    (event,) = api.publisher.sent
    assert event.approval_id == UUID(body["approval_id"])
    assert (event.tenant_id, event.venue_id, event.stamp_id) == (world.a.tenant_id, v1, STAMP)
    assert event.payload_sha256 == SHOWN


async def test_changed_draft_is_refused(api: Api, world: World) -> None:
    v1 = world.a.venues["v1"]
    draft = world.draft(world.a, v1, PAYLOAD)
    stale = payload_sha256({"to": "someone-else@example.test"})
    for path, body in (
        ("approve", {"shown_sha256": stale}),
        ("reject", {"shown_sha256": stale, "reason": "no"}),
    ):
        response = await api.client.post(
            f"/drafts/{draft}/{path}", json=body, headers=world.headers("approver", v1)
        )
        assert response.status_code == 409
        assert response.json()["code"] == "draft_changed"
    assert world.scalar("SELECT status FROM draft WHERE id = :d", d=draft) == "pending"


async def test_a_draft_is_decided_once(api: Api, world: World) -> None:
    v1 = world.a.venues["v1"]
    draft = world.draft(world.a, v1, PAYLOAD)
    headers = world.headers("approver", v1)
    first = await api.client.post(
        f"/drafts/{draft}/reject",
        json={"shown_sha256": SHOWN, "reason": "Wrong date"},
        headers=headers,
    )
    assert first.status_code == 200
    assert world.scalar("SELECT reason FROM approval WHERE draft_id = :d", d=draft) == "Wrong date"
    again = await api.client.post(
        f"/drafts/{draft}/approve", json={"shown_sha256": SHOWN}, headers=headers
    )
    assert again.status_code == 409
    assert again.json()["code"] == "draft_decided"
    (event,) = api.publisher.sent
    assert event.decision == "rejected"


async def test_edits_only_for_registered_types(api: Api, world: World) -> None:
    v1 = world.a.venues["v1"]
    headers = world.headers("owner", v1)
    plain = world.draft(world.a, v1, PAYLOAD)
    refused = await api.client.post(
        f"/drafts/{plain}/approve",
        json={"shown_sha256": SHOWN, "edited_payload": {"to": "x@example.test", "body": "b"}},
        headers=headers,
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "edit_not_supported"

    note = world.draft(world.a, v1, PAYLOAD, draft_type="test.note")
    invalid = await api.client.post(
        f"/drafts/{note}/approve",
        json={"shown_sha256": SHOWN, "edited_payload": {"to": "x", "body": "b", "bcc": "me"}},
        headers=headers,
    )
    assert invalid.status_code == 422
    assert "bcc" in invalid.json()["message"]

    edited = {"to": "guest@example.test", "body": "Edited reply"}
    ok = await api.client.post(
        f"/drafts/{note}/approve",
        json={"shown_sha256": SHOWN, "edited_payload": edited},
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json()["payload_sha256"] == payload_sha256(edited)
    assert world.scalar("SELECT edits->>'body' FROM approval WHERE draft_id = :d", d=note) == (
        "Edited reply"
    )
    # The draft itself is unchanged: the approval carries the edit.
    assert (
        world.scalar("SELECT payload->>'body' FROM draft WHERE id = :d", d=note) == PAYLOAD["body"]
    )


async def test_failed_publish_keeps_the_approval_and_can_be_retried(api: Api, world: World) -> None:
    v1 = world.a.venues["v1"]
    draft = world.draft(world.a, v1, PAYLOAD)
    headers = world.headers("approver", v1)
    api.publisher.fail = True
    response = await api.client.post(
        f"/drafts/{draft}/approve", json={"shown_sha256": SHOWN}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["event_published"] is False
    assert world.scalar("SELECT status FROM draft WHERE id = :d", d=draft) == "approved"

    api.publisher.fail = False
    retry = await api.client.post(f"/drafts/{draft}/approval/publish", headers=headers)
    assert retry.status_code == 200
    assert retry.json()["event_published"] is True
    (event,) = api.publisher.sent
    assert str(event.approval_id) == response.json()["approval_id"]


async def test_publish_needs_an_approved_draft(api: Api, world: World) -> None:
    v1 = world.a.venues["v1"]
    headers = world.headers("approver", v1)
    pending = world.draft(world.a, v1, PAYLOAD)
    assert (
        await api.client.post(f"/drafts/{pending}/approval/publish", headers=headers)
    ).status_code == 404
    rejected = world.draft(world.a, v1, PAYLOAD)
    await api.client.post(
        f"/drafts/{rejected}/reject", json={"shown_sha256": SHOWN, "reason": "no"}, headers=headers
    )
    response = await api.client.post(f"/drafts/{rejected}/approval/publish", headers=headers)
    assert response.status_code == 409


# --- runs and connections ------------------------------------------------------------------------


async def test_runs_show_tool_calls_and_drafts(api: Api, world: World) -> None:
    v1 = world.a.venues["v1"]
    run_id = uuid7()
    with world.admin.begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text(
                "INSERT INTO agent_run (tenant_id, id, venue_id, agent, prompt_version, model) "
                "VALUES (:t, :r, :v, 'events', 'v1', 'claude-sonnet')"
            ),
            {"t": world.a.tenant_id, "r": run_id, "v": v1},
        )
        conn.execute(
            text(
                "INSERT INTO tool_call "
                "(tenant_id, id, run_id, tool, category, status, args_summary) "
                "VALUES (:t, :id, :r, 'gmail_get_thread', 'read', 'succeeded', '{\"q\": \"x\"}')"
            ),
            {"t": world.a.tenant_id, "id": uuid7(), "r": run_id},
        )
    headers = world.headers("viewer", v1)
    runs = (await api.client.get("/runs", headers=headers)).json()
    assert str(run_id) in {r["id"] for r in runs}
    detail = (await api.client.get(f"/runs/{run_id}", headers=headers)).json()
    assert [c["tool"] for c in detail["tool_calls"]] == ["gmail_get_thread"]
    assert "args_summary" not in detail["tool_calls"][0]
    other = world.headers("manager2", world.a.venues["v2"])
    assert (await api.client.get(f"/runs/{run_id}", headers=other)).status_code == 404
