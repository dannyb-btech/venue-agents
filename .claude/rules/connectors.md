---
paths:
  - "apps/connectors/**"
  - "apps/sync-workers/**"
  - "apps/action-executor/**"
---

# Connector, sync and executor rules

- One MCP server per external system in `apps/connectors/<system>/`, built on `va_core.mcp`
  (`va-core[mcp]`, ADR-0018): a `ToolRegistry` served by `build_connector_server` + `connector_app`.
  Tool names: `<system>_<verb>_<noun>` (e.g. `gmail_search_threads`; Claude allows no dots).
- The tenant comes only from the run envelope the server verifies; argument models are
  `extra="forbid"` and never have `tenant_id`/`venue_id`/`stamp_id`/`run_id` fields (the registry refuses them).
  Handlers use the `ToolContext` they're given (`TenantSecrets`, `credentials`, `create_draft`), never
  a raw secret store or their own DB session.
- Connector tools are **read** or **draft** only (the registry refuses anything else). Draft tools
  call `ToolContext.create_draft` (type `<system>.<action>`) and return the ID; they never call a
  mutating external endpoint. Mutations live only in `apps/action-executor`.
- Credentials (`va_core.credentials`): `tool.credentials.secret()` / `access_token(refresh)`, from the
  venue's connection row; secrets named `venue_secret_name(...)`, refresh tokens ending `-token`.
  The framework serialises refresh per credential and stores rotated tokens; a `refresh` function
  only calls the vendor token endpoint (no DB, no secret store). Draft tools are `<system>_draft_<noun>`.
  Request the minimum OAuth scopes; document scopes in `docs/architecture/integrations.md`.
- Rate limits: per-tenant and per-app limits, backoff on 429, cache reads in Postgres
  (Xero charges by data egress; ADR-0011).
- Sync workers are incremental (`updated_since` cursors stored per tenant per system) and idempotent.
- Executor actions carry an idempotency key (`approval_id`) and write an `action_log` row with the
  external ID returned. Retry safely; never double-send.
- Tests use recorded fixtures (anonymised) under `tests/fixtures/<system>/`. No live API calls in CI.
