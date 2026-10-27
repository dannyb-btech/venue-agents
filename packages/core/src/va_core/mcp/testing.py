"""Test support: in-memory stores and a sample connector (one read tool, one draft tool).

Used by core's tests and `tests/isolation`. Never wire these into a deployed connector.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from va_core.credentials import ConnectionInfo
from va_core.errors import SecretNotFoundError
from va_core.http import HttpClient
from va_core.mcp.context import ToolContext
from va_core.mcp.drafts import NewDraft
from va_core.mcp.envelope import EnvelopeKeys
from va_core.mcp.registry import ToolRegistry
from va_core.tenancy import TenantContext

SAMPLE_SYSTEM = "sample"
TEST_KID = "test"


def make_keys(*kids: str) -> EnvelopeKeys:
    """Random 32-byte keys for `kids` (default one); the first kid signs."""
    names = kids or (TEST_KID,)
    return EnvelopeKeys({kid: SecretStr(secrets.token_hex(32)) for kid in names}, names[0])


class MemorySecretStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.reads: list[str] = []

    async def get(self, name: str) -> SecretStr:
        self.reads.append(name)
        if name not in self.values:
            raise SecretNotFoundError(f"secret {name!r} not found")
        return SecretStr(self.values[name])

    async def set(self, name: str, value: SecretStr) -> None:
        self.values[name] = value.get_secret_value()

    async def aclose(self) -> None:
        return None


class MemoryConnectionStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID, str], ConnectionInfo] = {}

    def add(self, ctx: TenantContext, info: ConnectionInfo) -> None:
        self.rows[(ctx.tenant_id, ctx.venue_id, info.system)] = info

    async def get(self, ctx: TenantContext, system: str) -> ConnectionInfo | None:
        return self.rows.get((ctx.tenant_id, ctx.venue_id, system))


@dataclass(frozen=True)
class RecordedDraft:
    id: UUID
    tenant_id: UUID
    venue_id: UUID
    run_id: UUID
    draft: NewDraft


@dataclass
class MemoryDraftWriter:
    drafts: list[RecordedDraft] = field(default_factory=list[RecordedDraft])

    async def create(self, ctx: TenantContext, run_id: UUID, draft: NewDraft) -> UUID:
        draft_id = uuid4()
        self.drafts.append(RecordedDraft(draft_id, ctx.tenant_id, ctx.venue_id, run_id, draft))
        return draft_id


class LocalRefreshLock:
    """One process-wide lock per key; records how many holders overlapped (should be 1)."""

    def __init__(self) -> None:
        self._locks: dict[tuple[UUID, str], asyncio.Lock] = {}
        self.max_concurrent = 0
        self._active = 0

    @asynccontextmanager
    async def hold(self, ctx: TenantContext, secret_ref: str) -> AsyncGenerator[None]:
        async with self._locks.setdefault((ctx.tenant_id, secret_ref), asyncio.Lock()):
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
            try:
                yield
            finally:
                self._active -= 1


# --- Sample connector ----------------------------------------------------------------------------


class GetNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$", description="Note ID in the sample API")


class Note(BaseModel):
    note_id: str
    title: str
    body: str
    tenant_id: str
    """Echoed for tests: the tenant the tool ran for (from the envelope)."""


class CreateNoteDraftArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class DraftCreated(BaseModel):
    draft_id: str


def sample_registry(transport: httpx.AsyncBaseTransport) -> ToolRegistry:
    """A connector for a fake notes API reached through `transport` (e.g. `httpx.MockTransport`)."""
    registry = ToolRegistry(SAMPLE_SYSTEM)

    @registry.read(
        "sample_get_note", args=GetNoteArgs, result=Note, description="Read one note by ID."
    )
    async def get_note(args: GetNoteArgs, tool: ToolContext) -> Note:  # pyright: ignore[reportUnusedFunction]
        api_key = await tool.credentials.secret()
        async with HttpClient(
            SAMPLE_SYSTEM,
            base_url="https://sample.invalid",
            transport=transport,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
        ) as client:
            response = await client.request("GET", f"/notes/{args.note_id}")
        data = response.json()
        return Note(
            note_id=args.note_id,
            title=str(data["title"]),
            body=str(data["body"]),
            tenant_id=str(tool.tenant.tenant_id),
        )

    @registry.draft(
        "sample_draft_note",
        args=CreateNoteDraftArgs,
        result=DraftCreated,
        description="Draft a new note for a human to approve. Does not create it.",
    )
    async def draft_note(  # pyright: ignore[reportUnusedFunction]
        args: CreateNoteDraftArgs, tool: ToolContext
    ) -> DraftCreated:
        draft_id = await tool.create_draft(
            NewDraft(
                type="sample.note",
                payload={"title": args.title, "body": args.body},
                reasoning_summary="Sample draft",
            )
        )
        return DraftCreated(draft_id=str(draft_id))

    return registry
