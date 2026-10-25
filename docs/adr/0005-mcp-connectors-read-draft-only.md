# 0005. MCP connectors are read + draft only; a separate executor holds write scopes

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO

## Context

Microsoft recommends MCP for tool and data access with typed payloads and human approval for
high-impact actions ([multi-agent patterns](https://learn.microsoft.com/agents/architecture/multi-agent-patterns));
Foundry's MCP guidance says allow-list tools, treat tool results as untrusted, and require approval for writes
([MCP best practices](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol#best-practices)).

## Decision

- One MCP server per external system (`apps/connectors/<system>`), exposing **read** and **draft** tools only.
- Draft tools write a `draft` row; they never call mutating vendor endpoints.
- `apps/action-executor` is the **only** component with write-scoped credentials. It acts only on an
  approved draft, with `approval_id` as the idempotency key, and logs every action.
- Agents get an explicit per-agent tool allow-list.

## Consequences

- A compromised or confused agent cannot send, order, pay or publish.
- Two code paths per system (read/draft vs execute) to maintain.

## Revisit when

A specific low-risk action is proven safe to automate (decided per action via a new ADR).
