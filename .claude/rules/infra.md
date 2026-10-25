---
paths:
  - "infra/**"
  - ".github/workflows/**"
---

# Infrastructure rules

- Bicep only. `infra/bicep/global/` (Front Door, API Management, catalog DB, Foundry connection),
  `infra/bicep/stamp/` (Container Apps env, Postgres, Key Vault, Service Bus, Blob, Durable Task
  Scheduler, App Insights), `infra/bicep/params/` one file per stamp (`canary`, `general-01`, ...).
- A stamp must be fully deployable from its params file alone; no hand-made resources.
- Region: Australia East for everything except Foundry Claude deployments (supported regions, ADR-0002).
- Managed identities and RBAC; no keys where Entra auth exists. Private endpoints for data services.
- Validate with `az bicep build` and `az bicep lint`. **Never run deployments** (`az deployment`,
  `azd up/provision/deploy` are denied). Humans deploy via the pipeline.
- Use the `azure-researcher` subagent (Microsoft Learn MCP, Bicep schema tool) for resource schemas
  and API versions instead of memory.
