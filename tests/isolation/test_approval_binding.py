"""Approvals bind to content; drafts freeze once decided (P0-03 security review H2, migration 0007).

Run as the app role, the way the Platform API and the agent runtime reach these tables.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from tests.isolation.support import Seeded, as_tenant
from va_db.ids import uuid7

_DRAFT = text(
    "INSERT INTO draft (id, venue_id, type, status, payload) "
    "VALUES (:id, :venue, 'gmail.reply', :status, CAST(:payload AS jsonb))"
)
_HASH = "a" * 64


@pytest.fixture
def conn(seeded: Seeded) -> Iterator[Connection]:
    with as_tenant(seeded.app, seeded.a.tenant_id) as conn:
        yield conn


def _draft(conn: Connection, seeded: Seeded, status: str = "pending") -> UUID:
    draft_id = uuid7()
    conn.execute(
        _DRAFT,
        {"id": draft_id, "venue": seeded.a.refs["venue"], "status": status, "payload": "{}"},
    )
    return draft_id


def _set_status(conn: Connection, draft_id: UUID, status: str) -> None:
    conn.execute(text("UPDATE draft SET status = :s WHERE id = :id"), {"s": status, "id": draft_id})


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        (("approved",), True),
        (("rejected",), True),
        (("superseded",), True),
        (("approved", "executed"), True),
        (("approved", "failed"), True),
        (("executed",), False),
        (("rejected", "approved"), False),
        (("approved", "pending"), False),
        (("approved", "executed", "approved"), False),
    ],
)
def test_status_transitions(
    conn: Connection, seeded: Seeded, path: tuple[str, ...], allowed: bool
) -> None:
    draft_id = _draft(conn, seeded)
    *ok, last = path
    for status in ok:
        _set_status(conn, draft_id, status)
    if allowed:
        _set_status(conn, draft_id, last)
    else:
        with pytest.raises(DBAPIError, match="transition not allowed"):
            _set_status(conn, draft_id, last)


def test_pending_payload_may_change_but_decided_payload_may_not(
    conn: Connection, seeded: Seeded
) -> None:
    draft_id = _draft(conn, seeded)
    update = text("UPDATE draft SET payload = CAST(:p AS jsonb) WHERE id = :id")
    conn.execute(update, {"p": '{"v": 1}', "id": draft_id})
    _set_status(conn, draft_id, "approved")
    with pytest.raises(DBAPIError, match="decided draft cannot change"):
        conn.execute(update, {"p": '{"v": 2}', "id": draft_id})


@pytest.mark.parametrize("column", ["type", "venue_id", "run_id"])
def test_identity_columns_never_change(conn: Connection, seeded: Seeded, column: str) -> None:
    draft_id = _draft(conn, seeded)
    value = {
        "type": "'xero.bill'",
        "venue_id": "gen_random_uuid()",
        "run_id": f"'{seeded.a.refs['agent_run']}'::uuid",
    }[column]
    with pytest.raises(DBAPIError, match="identity columns are immutable"):
        conn.execute(text(f"UPDATE draft SET {column} = {value} WHERE id = :id"), {"id": draft_id})  # noqa: S608


def test_only_pending_drafts_can_be_deleted(conn: Connection, seeded: Seeded) -> None:
    pending = _draft(conn, seeded)
    conn.execute(text("DELETE FROM draft WHERE id = :id"), {"id": pending})
    decided = _draft(conn, seeded)
    _set_status(conn, decided, "rejected")
    with pytest.raises(DBAPIError, match="cannot be deleted"):
        conn.execute(text("DELETE FROM draft WHERE id = :id"), {"id": decided})


_APPROVAL = text(
    "INSERT INTO approval (id, draft_id, user_id, decision, payload_sha256) "
    "VALUES (:id, :d, :u, :decision, :h)"
)


@pytest.mark.parametrize(
    ("decision", "digest", "ok"),
    [
        ("approved", _HASH, True),
        ("approved", None, False),
        ("approved", "A" * 64, False),
        ("approved", "a" * 63, False),
        ("rejected", None, True),
        ("rejected", _HASH, True),
    ],
)
def test_approval_hash_is_required_for_approvals(
    conn: Connection, seeded: Seeded, decision: str, digest: str | None, ok: bool
) -> None:
    params = {
        "id": uuid7(),
        "d": _draft(conn, seeded),
        "u": seeded.a.refs["app_user"],
        "decision": decision,
        "h": digest,
    }
    if ok:
        conn.execute(_APPROVAL, params)
    else:
        with pytest.raises(DBAPIError, match="payload_sha256"):
            conn.execute(_APPROVAL, params)


def test_a_draft_needs_an_approval_to_be_approved(conn: Connection, seeded: Seeded) -> None:
    # The check is deferred to commit; IMMEDIATE runs it now, inside the rolled-back transaction.
    conn.execute(text("SET CONSTRAINTS va_draft_approval_required IMMEDIATE"))
    bare = _draft(conn, seeded)
    with pytest.raises(DBAPIError, match="needs an approval"), conn.begin_nested():
        _set_status(conn, bare, "approved")
    with pytest.raises(DBAPIError, match="needs an approval"), conn.begin_nested():
        _draft(conn, seeded, status="approved")

    rejected = _draft(conn, seeded)
    conn.execute(
        _APPROVAL,
        {
            "id": uuid7(),
            "d": rejected,
            "u": seeded.a.refs["app_user"],
            "decision": "rejected",
            "h": None,
        },
    )
    with pytest.raises(DBAPIError, match="needs an approval"), conn.begin_nested():
        _set_status(conn, rejected, "approved")

    approved = _draft(conn, seeded)
    conn.execute(
        _APPROVAL,
        {
            "id": uuid7(),
            "d": approved,
            "u": seeded.a.refs["app_user"],
            "decision": "approved",
            "h": _HASH,
        },
    )
    _set_status(conn, approved, "approved")
