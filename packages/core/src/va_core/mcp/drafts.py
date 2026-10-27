"""Draft helper: what draft tools write instead of calling a mutating vendor endpoint (ADR-0005)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from va_core.tenancy import TenantContext


class NewDraft(BaseModel):
    """A proposed action. `type` is `<system>.<action>`; the executor dispatches on it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = Field(pattern=r"^[a-z][a-z0-9]*\.[a-z][a-z0-9_]{0,63}$")
    payload: dict[str, JsonValue]
    reasoning_summary: str | None = Field(default=None, max_length=4000)
    sources: list[dict[str, JsonValue]] = Field(default_factory=list[dict[str, JsonValue]])


class DraftWriter(Protocol):
    """Writes a `pending` draft row for `ctx`'s tenant and venue; returns its ID."""

    async def create(self, ctx: TenantContext, run_id: UUID, draft: NewDraft) -> UUID: ...
