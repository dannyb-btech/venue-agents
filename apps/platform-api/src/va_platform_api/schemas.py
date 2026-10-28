"""Request and response bodies for the Platform API. No body ever carries a tenant ID."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from va_core.payloads import SHA256_HEX
from va_core.tenancy import Role
from va_db.models import ConnectionStatus, DraftStatus, RunStatus

Sha256 = Annotated[str, StringConstraints(pattern=SHA256_HEX.pattern)]
JsonObject = dict[str, object]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class ErrorOut(_Out):
    code: str
    message: str


class VenueOut(_Out):
    venue_id: UUID
    name: str
    roles: list[Role]


class MeOut(_Out):
    user_id: UUID
    display_name: str | None
    email: str | None
    venues: list[VenueOut]


class DraftSummaryOut(_Out):
    id: UUID
    type: str
    status: DraftStatus
    run_id: UUID | None
    created_at: datetime


class DraftOut(DraftSummaryOut):
    payload: JsonObject
    payload_sha256: Sha256
    """Send this back as `shown_sha256` when deciding, so a changed draft is refused."""
    reasoning_summary: str | None
    sources: list[JsonObject]
    editable: bool
    """Whether `edited_payload` is accepted for this draft type."""


class ApproveIn(_In):
    shown_sha256: Sha256
    edited_payload: JsonObject | None = None


class RejectIn(_In):
    shown_sha256: Sha256
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class DecisionOut(_Out):
    draft_id: UUID
    approval_id: UUID
    status: DraftStatus
    payload_sha256: Sha256
    event_published: bool | None = None
    """Approvals only: False means the approval is recorded but the executor wasn't told yet;
    call `POST /drafts/{id}/approval/publish`."""


class ConnectionOut(_Out):
    system: str
    status: ConnectionStatus
    scopes: list[str]
    updated_at: datetime


class OAuthStartOut(_Out):
    authorize_url: str
    expires_at: datetime


class OAuthCallbackIn(_In):
    code: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    state: Annotated[str, StringConstraints(min_length=16, max_length=256)]


class RunSummaryOut(_Out):
    id: UUID
    agent: str
    status: RunStatus
    model: str
    prompt_version: str
    error_code: str | None
    started_at: datetime
    ended_at: datetime | None


class ToolCallOut(_Out):
    tool: str
    category: str
    status: str
    duration_ms: int | None
    created_at: datetime


class RunOut(RunSummaryOut):
    tool_calls: list[ToolCallOut] = Field(default_factory=list[ToolCallOut])
    drafts: list[DraftSummaryOut] = Field(default_factory=list[DraftSummaryOut])
