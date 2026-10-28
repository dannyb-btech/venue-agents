"""`approval.granted` events for the action executor (ADR-0006, ADR-0019).

Published after the approval commits, with `MessageId` = approval id, so a re-send (after a failed
publish, `POST /drafts/{id}/approval/publish`) is a duplicate Service Bus can drop and the executor
ignores (idempotent on approval id, P0-09). The event names records; it carries no payload: the
executor reads the draft and approval and checks `payload_sha256` itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from azure.identity.aio import ManagedIdentityCredential
from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient
from pydantic import BaseModel, ConfigDict

APPROVAL_GRANTED = "approval.granted"


class ApprovalGranted(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: UUID
    draft_id: UUID
    draft_type: str
    tenant_id: UUID
    venue_id: UUID
    stamp_id: str
    payload_sha256: str
    decided_at: datetime


class ApprovalPublisher(Protocol):
    async def publish(self, event: ApprovalGranted) -> None:
        """Send the event. Raises on failure; the caller reports it, never retries blindly."""
        ...

    async def aclose(self) -> None: ...


class InMemoryPublisher:
    """Tests and local runs without the Service Bus emulator."""

    def __init__(self) -> None:
        self.sent: list[ApprovalGranted] = []
        self.fail = False

    async def publish(self, event: ApprovalGranted) -> None:
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

    async def publish(self, event: ApprovalGranted) -> None:
        message = ServiceBusMessage(
            event.model_dump_json(),
            message_id=str(event.approval_id),
            subject=APPROVAL_GRANTED,
            content_type="application/json",
            application_properties={"stamp_id": event.stamp_id},
        )
        async with self._client.get_topic_sender(topic_name=self._topic) as sender:
            await sender.send_messages(message)

    async def aclose(self) -> None:
        await self._client.close()
        if self._credential is not None:
            await self._credential.close()
