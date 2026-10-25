# Data model

Per stamp: one Postgres server (→ elastic cluster sharded by `tenant_id` when needed). Every tenant
table has `tenant_id` in its primary and foreign keys and an RLS policy (ADR-0007, ADR-0008).
Global: the catalog database (control plane).

## Stores

| Store | Service | Holds |
| --- | --- | --- |
| Core DB | Postgres flexible server | Platform, synced business data, agent activity |
| Vectors | pgvector (same server) | Embeddings of menus, past proposals, supplier docs, tasting notes |
| Files | Blob (container per tenant) | Uploads, generated POs/rosters, Toast SFTP exports |
| Secrets | Key Vault | OAuth refresh tokens and API keys per tenant per system |
| Workflow state | Durable Task Scheduler | Orchestration checkpoints, approval waits |
| Messaging | Service Bus | Webhooks, triggers, sync jobs |
| Catalog | Postgres (global) | Venue → stamp, region, plan, entitlements |
| Analytics | Central store (Fabric or replica) | Cross-venue reporting; no app query crosses venues |

## Schema groups (stamp DB)

**Platform:** `tenant`, `venue`, `app_user`, `role_assignment`, `connection` (system, status, scopes,
secret_ref), `agent_config` (targets e.g. labour %, wine GP, approval rules, schedules).

**Synced business data:** `guest`, `reservation`, `event_enquiry`, `sale_line`, `menu_item`,
`stock_level`, `supplier`, `price_list_item`, `purchase_order`, `employee`, `shift`, `timesheet`,
`pay_run_summary`. Each has `source_system`, `source_id`, `synced_at`, unique on
`(tenant_id, source_system, source_id)`. Payroll kept summarised unless a task needs line level.

**Sync state:** `sync_cursor` (tenant, system, stream, cursor, last_success_at, last_error).

**Agent activity:** `agent_run` (agent, prompt_version, model, status, started/ended, cost),
`tool_call`, `draft` (type, payload JSON, status, reasoning summary, sources), `approval`
(user, decision, edits, reason, at), `action_log` (external_id, result, at), `usage_cost`
(tokens in/out/cached, cost, venue_id).

**Knowledge:** `document` (source, title, blob_ref), `chunk` (text, embedding vector, document_id).

## Catalog DB (global)

`venue_directory` (venue_id, tenant_id, stamp_id, region, plan, status), `stamp`
(id, region, capacity, ring, status), `entitlement`, `onboarding_job`, `move_job`.

## Conventions

- Primary keys `(tenant_id, id)`, ids are UUIDv7. Timestamps `timestamptz` UTC.
- Indexes start with `tenant_id`. Money as `numeric(12,2)` with currency column (AUD default).
