"""Deciding drafts (ADR-0006): approve, reject, re-publish.

An approval is bound to content (P0-03 review H2): the approver sends the hash of the payload they
were shown, and a draft that changed since is refused. The recorded `payload_sha256` is the hash of
what will be executed: the edited payload if there is one, else the draft's. The database freezes
the draft once it leaves `pending` (`va_draft_guard`).

Edits are accepted only for draft types with a registered payload model, so an approver can't
smuggle fields the executor would act on. None are registered yet; connector tasks add them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from va_core.errors import NotFoundError
from va_core.payloads import canonical_json, payload_sha256
from va_core.telemetry import get_logger
from va_core.tenancy import TenantContext
from va_db.models import Approval, Decision, Draft, DraftStatus
from va_db.session import tenant_session
from va_platform_api.errors import (
    DraftChangedError,
    DraftDecidedError,
    EditNotSupportedError,
    UnprocessableError,
)
from va_platform_api.events import ApprovalGranted, ApprovalPublisher

logger = get_logger(__name__)

MAX_EDIT_BYTES = 64 * 1024

type EditableTypes = Mapping[str, type[BaseModel]]
"""Draft type -> the model an edited payload must satisfy (`extra="forbid"`)."""


@dataclass(frozen=True)
class Decided:
    draft_id: UUID
    approval_id: UUID
    status: DraftStatus
    payload_sha256: str
    event_published: bool | None = None


def _validated_edit(
    draft_type: str, edited: dict[str, object], editable: EditableTypes
) -> dict[str, object]:
    model = editable.get(draft_type)
    if model is None:
        raise EditNotSupportedError("this draft type can't be edited; approve or reject it")
    if len(canonical_json(edited)) > MAX_EDIT_BYTES:
        raise UnprocessableError("edited payload is too large")
    try:
        return model.model_validate(edited).model_dump(mode="json")
    except ValidationError as exc:
        # Field names only; values may be personal data.
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise UnprocessableError(f"edited payload is invalid: {', '.join(fields)}") from exc


async def _pending_draft(session: AsyncSession, ctx: TenantContext, draft_id: UUID) -> Draft:
    draft = await session.scalar(
        select(Draft).where(Draft.id == draft_id, Draft.venue_id == ctx.venue_id).with_for_update()
    )
    if draft is None:
        raise NotFoundError("draft not found")
    if draft.status != DraftStatus.PENDING:
        raise DraftDecidedError(f"draft is already {draft.status}")
    return draft


def _check_shown(draft: Draft, shown_sha256: str) -> str:
    current = payload_sha256(draft.payload)
    if current != shown_sha256:
        raise DraftChangedError("the draft changed since it was shown; reload it")
    return current


class DraftDecisions:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        publisher: ApprovalPublisher,
        editable: EditableTypes,
    ) -> None:
        self._sessions = sessions
        self._publisher = publisher
        self._editable = editable

    def is_editable(self, draft_type: str) -> bool:
        return draft_type in self._editable

    async def approve(
        self,
        ctx: TenantContext,
        draft_id: UUID,
        shown_sha256: str,
        edited: dict[str, object] | None,
    ) -> Decided:
        async with tenant_session(self._sessions, ctx) as session:
            draft = await _pending_draft(session, ctx, draft_id)
            _check_shown(draft, shown_sha256)
            final = (
                _validated_edit(draft.type, edited, self._editable)
                if edited is not None
                else draft.payload
            )
            digest = payload_sha256(final)
            approval = Approval(
                draft_id=draft.id,
                user_id=UUID(str(ctx.user_id)),
                decision=Decision.APPROVED.value,
                edits=final if edited is not None else None,
                payload_sha256=digest,
            )
            session.add(approval)
            draft.status = DraftStatus.APPROVED.value
            await session.flush()
            await session.refresh(approval, ["decided_at"])
            event = _event(ctx, draft, approval, digest)
        published = await self._publish(event)
        return Decided(draft.id, approval.id, DraftStatus.APPROVED, digest, published)

    async def reject(
        self, ctx: TenantContext, draft_id: UUID, shown_sha256: str, reason: str
    ) -> Decided:
        async with tenant_session(self._sessions, ctx) as session:
            draft = await _pending_draft(session, ctx, draft_id)
            digest = _check_shown(draft, shown_sha256)
            approval = Approval(
                draft_id=draft.id,
                user_id=UUID(str(ctx.user_id)),
                decision=Decision.REJECTED.value,
                reason=reason,
                payload_sha256=digest,
            )
            session.add(approval)
            draft.status = DraftStatus.REJECTED.value
            await session.flush()
        return Decided(draft.id, approval.id, DraftStatus.REJECTED, digest)

    async def republish(self, ctx: TenantContext, draft_id: UUID) -> Decided:
        """Re-send `approval.granted` for an approved draft the executor hasn't acted on."""
        async with tenant_session(self._sessions, ctx) as session:
            row = (
                await session.execute(
                    select(Draft, Approval)
                    .join(Approval, Approval.draft_id == Draft.id)
                    .where(Draft.id == draft_id, Draft.venue_id == ctx.venue_id)
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("no approval for this draft")
        draft, approval = row
        if draft.status != DraftStatus.APPROVED or approval.payload_sha256 is None:
            raise DraftDecidedError(f"draft is {draft.status}; nothing to publish")
        event = _event(ctx, draft, approval, approval.payload_sha256)
        published = await self._publish(event)
        return Decided(
            draft.id, approval.id, DraftStatus.APPROVED, approval.payload_sha256, published
        )

    async def _publish(self, event: ApprovalGranted) -> bool:
        try:
            await self._publisher.publish(event)
        except Exception as exc:
            logger.warning(
                "approval event not published (%s); approval %s is recorded",
                type(exc).__name__,
                event.approval_id,
            )
            return False
        return True


def _event(ctx: TenantContext, draft: Draft, approval: Approval, digest: str) -> ApprovalGranted:
    return ApprovalGranted(
        approval_id=approval.id,
        draft_id=draft.id,
        draft_type=draft.type,
        tenant_id=ctx.tenant_id,
        venue_id=ctx.venue_id,
        stamp_id=ctx.stamp_id,
        payload_sha256=digest,
        decided_at=approval.decided_at,
    )
