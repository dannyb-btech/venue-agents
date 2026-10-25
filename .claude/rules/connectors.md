---
paths:
  - "apps/connectors/**"
  - "apps/sync-workers/**"
  - "apps/action-executor/**"
---

# Connector, sync and executor rules

- One MCP server per external system in `apps/connectors/<system>/`, built on the shared base in
  `packages/core.mcp`. Tool names: `<system>.<verb>_<noun>` (e.g. `gmail.search_threads`).
- Connector tools are **read** or **draft** only. Draft tools write a `draft` row and return its ID;
  they never call a mutating external endpoint. Mutations live only in `apps/action-executor`.
- Credentials: fetched per tenant from the secrets interface using the `connection` row's secret ref.
  Request the minimum OAuth scopes; document scopes in `docs/architecture/integrations.md`.
- Rate limits: per-tenant and per-app limits, backoff on 429, cache reads in Postgres
  (Xero charges by data egress; ADR-0011).
- Sync workers are incremental (`updated_since` cursors stored per tenant per system) and idempotent.
- Executor actions carry an idempotency key (`approval_id`) and write an `action_log` row with the
  external ID returned. Retry safely; never double-send.
- Tests use recorded fixtures (anonymised) under `tests/fixtures/<system>/`. No live API calls in CI.
