# Glossary

| Term | Meaning |
| --- | --- |
| Venue | A single restaurant/bar location. |
| Tenant | The customer account owning one or more venues; isolation boundary in the data. |
| Stamp | A full copy of the stack (Container Apps env, Postgres, Key Vault, ...) serving a group of venues. |
| Canary stamp | The stamp for OR and internal testing; receives changes first. |
| Control plane | Global services: tenant catalog, placement, onboarding, move-venue. |
| Tenant catalog | Global DB mapping venue → stamp, plan, entitlements. |
| Agent | A role (Events Manager, Wine Buyer, Office Manager, ...) built with Agent Framework. |
| Agent run | One durable orchestration of an agent for one venue. |
| TenantContext | `va_core.tenancy`: tenant, venue, stamp, user and roles, resolved once at the edge; never from input. |
| Tenant scope | `tenant_scope(ctx)`: sets the current tenant for telemetry; switching tenant inside one raises. |
| Connector | MCP server for one external system; read + draft tools only. |
| Draft | A proposed action (email, order, roster, payment link) awaiting approval. |
| Approval | A recorded decision on a draft by an authorised user (approve, edit, reject). |
| Action | The external side effect performed by the executor after approval. |
| Executor | `apps/action-executor`, the only component with write scopes. |
| AI gateway | API Management in front of Claude on Foundry: token limits, cost, pooling. |
| RMS | Toast Restaurant Management Suite (tier gates standard API access). |
| Labour % | Labour cost as a percentage of sales; computed in code. |
| GP | Gross profit margin (e.g. wine list targets). |
| EDM | Email marketing campaign (SevenRooms). |
