# Architecture overview

Multi-tenant agent platform on Azure. Each venue is a tenant; venues are grouped onto deployment stamps;
role agents read venue systems through connectors, produce drafts, and only an executor acts after a
manager approves. Decisions: `docs/adr/`.

## Components

```
                     Venue app (PWA)          Admin console
                            │                       │
                    Azure Front Door (routes venue → stamp via catalog)
                            │
        ┌──────── Global ───┴─────────────────────────────────────────┐
        │ Control plane: tenant catalog, onboarding, move-venue        │
        │ API Management AI gateway → Claude on Foundry (Global Std)   │
        │ Central analytics store                                      │
        └──────────────────────────────────────────────────────────────┘
                            │
        ┌──────── Stamp (Australia East), repeated ──────────────────┐
        │ Container Apps: Platform API · Agent runtime (Agent         │
        │   Framework + Durable Task) · Connector MCP servers ·       │
        │   Sync workers · Action executor                            │
        │ Data: Postgres + pgvector (RLS) · Blob · Key Vault ·        │
        │   Service Bus · Durable Task Scheduler · App Insights       │
        └─────────────────────────────────────────────────────────────┘
                            │ (executor only writes; connectors read/draft)
        Google · SevenRooms · Toast · Tanda · Xero · Stripe · Meta · Webflow
```

| Component | Responsibility | ADR |
| --- | --- | --- |
| Platform API | Tenants, users, connections (OAuth), agent config, drafts, approvals, runs | 0009 |
| Control plane | Tenant catalog (venue → stamp, plan, entitlements), placement, onboarding/offboarding, move-venue | 0008 |
| Agent runtime | One Agent Framework agent per role; durable orchestrations; approval waits | 0004 |
| Connectors | One MCP server per system; read + draft tools | 0005 |
| Sync workers | Incremental ingestion (webhooks where offered, else polling) | 0011 |
| Action executor | Executes approved drafts with write scopes; idempotent; logged | 0005, 0006 |
| AI gateway | Per-venue token limits, cost attribution, backend pool across Foundry deployments | 0003 |
| Venue app | Approvals inbox, activity, connections, targets, hours-saved report | 0006 |

## How an agent run works

1. A trigger (schedule staggered per venue, webhook, inbox event) puts a message on Service Bus.
2. The agent runtime starts a durable orchestration for that tenant (tenant context from the message
   envelope signed by the platform, never from content).
3. Activities gather context deterministically (SQL over synced data, connector reads). Every call to a
   connector MCP server carries a signed run envelope for that tenant, venue and run (ADR-0018).
4. The Agent Framework agent calls Claude through the gateway and uses its allow-listed tools;
   draft tools create `draft` rows.
5. The orchestration notifies the venue and waits (zero compute) for the approval event.
6. On approval, the executor performs the action with the tenant's write credentials, idempotent on
   `approval_id`, and writes `action_log`. Rejections and edits feed evals.
7. Every step is traced in App Insights with venue_id and run_id; tokens and cost are recorded.

## Detail docs

- [Data model](data-model.md) · [APIs](api.md) · [Agents](agents.md) · [Integrations](integrations.md)
- [Security, privacy and multi-tenancy](security.md) · [Scale plan](scale-plan.md)
