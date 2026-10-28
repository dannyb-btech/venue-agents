"""`approval.decided` events: the stamp's approvals topic (ADR-0006, ADR-0019).

Published after the decision commits, with `MessageId` = approval id. Two subscriptions: the action
executor (acts on `approved`) and the agent runtime (resumes the waiting orchestration, P0-08).
The event is a pointer, not a source of truth: it carries no payload, and consumers re-read the
draft and approval under RLS and check `payload_sha256` before acting. Consumers are idempotent on
the approval id; Service Bus duplicate detection only trims re-sends inside its window. How a
consumer trusts the event's tenant is a P0-08/P0-09 decision (phase file).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from azure.identity.aio import ManagedIdentityCredential
from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient
from pydantic import BaseModel, ConfigDict

from va_db.models import Decision

APPROVAL_DECIDED = "approval.decided"
PUBLISH_TIMEOUT = 10.0
"""Seconds before a publish counts as failed; the decision stands and can be re-sent."""


class ApprovalDecided(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: UUID
    decision: Decision
    draft_id: UUID
    draft_type: str
    tenant_id: UUID
    venue_id: UUID
    stamp_id: str
    payload_sha256: str
    decided_at: datetime


class ApprovalPublisher(Protocol):
    async def publish(self, event: ApprovalDecided) -> None:
        """Send the event. Raises on failure; the caller reports it, never retries blindly."""
        ...

    async def aclose(self) -> None: ...


class InMemoryPublisher:
    """Tests and local runs without the Service Bus emulator."""

    def __init__(self) -> None:
        self.sent: list[ApprovalDecided] = []
        self.fail = False

    async def publish(self, event: ApprovalDecided) -> None:
        if self.fail:
            raise ConnectionError("publish failed (test)")
        self.sent.append(event)

    async def aclose(self) -> None:
        return None


class ServiceBusApprovalPublisher:
    """Sends to the stamp's approvals topic. Build with `from_connection_string` (emulator) or
    `from_namespace` (Azure, managed identity)."""

    def __init__(
        self,
        client: ServiceBusClient,
        topic: str,
        credential: ManagedIdentityCredential | None = None,
    ) -> None:
        self._client = client
        self._topic = topic
        self._credential = credential

    @classmethod
    def from_connection_string(
        cls, connection_string: str, topic: str
    ) -> ServiceBusApprovalPublisher:
        return cls(ServiceBusClient.from_connection_string(connection_string), topic)

    @classmethod
    def from_namespace(
        cls, namespace: str, topic: str, client_id: str | None
    ) -> ServiceBusApprovalPublisher:
        credential = ManagedIdentityCredential(client_id=client_id)
        client = ServiceBusClient(fully_qualified_namespace=namespace, credential=credential)
        return cls(client, topic, credential)

    async def publish(self, event: ApprovalDecided) -> None:
        message = ServiceBusMessage(
            event.model_dump_json(),
            message_id=str(event.approval_id),
            subject=APPROVAL_DECIDED,
            content_type="application/json",
            application_properties={"stamp_id": event.stamp_id, "decision": event.decision.value},
        )
        async with self._client.get_topic_sender(topic_name=self._topic) as sender:
            await sender.send_messages(message)

    async def aclose(self) -> None:
        await self._client.close()
        if self._credential is not None:
            await self._credential.close()
