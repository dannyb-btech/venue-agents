"""What a connector tool handler receives besides its arguments.

Built per call by the server from the verified run envelope. Everything in it is already bound to
the envelope's tenant, venue and run: secrets through `TenantSecrets` (never the raw store),
credentials through a connection lookup for this venue, and drafts through a closure that writes for
this tenant, venue and run only. Read tools get no way to create drafts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from va_core.credentials import TenantCredentials
from va_core.errors import AuthorizationError
from va_core.mcp.drafts import NewDraft
from va_core.secrets import TenantSecrets
from va_core.tenancy import TenantContext
from va_core.tools import ToolCategory

type DraftCreator = Callable[[NewDraft], Awaitable[UUID]]


@dataclass(frozen=True)
class ToolContext:
    tenant: TenantContext
    run_id: UUID
    agent: str
    system: str
    tool: str
    category: ToolCategory
    secrets: TenantSecrets
    credentials: TenantCredentials
    _create_draft: DraftCreator | None
    """Bound to this call's tenant, venue and run; None for read tools."""

    async def create_draft(self, draft: NewDraft) -> UUID:
        """Record a pending draft for this tenant, venue and run. Draft tools only."""
        if self.category is not ToolCategory.DRAFT or self._create_draft is None:
            raise AuthorizationError(f"tool {self.tool!r} is a read tool and cannot create drafts")
        if not draft.type.startswith(f"{self.system}."):
            raise AuthorizationError(
                f"tool {self.tool!r} can only create {self.system}.* drafts, not {draft.type!r}"
            )
        return await self._create_draft(draft)
