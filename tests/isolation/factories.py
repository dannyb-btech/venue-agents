"""One row factory per stamp table, in insert (parent-first) order.

`test_every_table_has_a_factory` fails when a table has no factory here, so a new table can't skip
the isolation suite. Each factory takes the tenant and the IDs of that tenant's parent rows, and
returns fresh unique values on every call.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from uuid import UUID, uuid4

from va_db.ids import uuid7

type Row = dict[str, object]
type Refs = dict[str, UUID]
type Factory = Callable[[UUID, Refs], Row]


def _u() -> str:
    return uuid4().hex[:12]


FACTORIES: dict[str, Factory] = {
    "tenant": lambda t, _r: {"tenant_id": t, "name": f"tenant {_u()}"},
    "venue": lambda t, _r: {"tenant_id": t, "id": uuid7(), "name": f"venue {_u()}"},
    "app_user": lambda t, _r: {
        "tenant_id": t,
        "id": uuid7(),
        "external_subject": f"sub-{_u()}",
        "email": f"{_u()}@example.test",
    },
    "role_assignment": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "user_id": r["app_user"],
        "venue_id": r["venue"],
        "role": "approver",
    },
    "connection": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "venue_id": r["venue"],
        "system": f"gmail-{_u()}",
        "secret_ref": f"conn-{_u()}",
    },
    "agent_config": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "venue_id": r["venue"],
        "agent": f"events-{_u()}",
        "config": {"labour_pct_target": 28},
    },
    "agent_run": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "venue_id": r["venue"],
        "agent": "events",
        "prompt_version": "v1",
        "model": "claude-sonnet",
    },
    "tool_call": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "run_id": r["agent_run"],
        "tool": "gmail_search_threads",
        "category": "read",
        "status": "succeeded",
    },
    "draft": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "venue_id": r["venue"],
        "run_id": r["agent_run"],
        "type": "email_reply",
        "payload": {"subject": "Re: function enquiry"},
    },
    "approval": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "draft_id": r["draft"],
        "user_id": r["app_user"],
        "decision": "approved",
    },
    "action_log": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "approval_id": r["approval"],
        "draft_id": r["draft"],
        "status": "succeeded",
    },
    "usage_cost": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "venue_id": r["venue"],
        "run_id": r["agent_run"],
        "model": "claude-sonnet",
        "input_tokens": 1200,
        "output_tokens": 300,
        "cost": Decimal("0.004215"),
    },
    "sync_cursor": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "venue_id": r["venue"],
        "system": "tanda",
        "stream": f"shifts-{_u()}",
    },
    "document": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "venue_id": r["venue"],
        "source_system": "drive",
        "source_id": f"file-{_u()}",
        "title": "Function menu",
    },
    "chunk": lambda t, r: {
        "tenant_id": t,
        "id": uuid7(),
        "document_id": r["document"],
        "ordinal": 0,
        "text": "Canapés, three courses",
        "embedding": [0.1, 0.2, 0.3],
    },
}


def row_id(table: str, row: Row) -> UUID:
    value = row["tenant_id"] if table == "tenant" else row["id"]
    assert isinstance(value, UUID)
    return value
