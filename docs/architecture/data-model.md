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

- Primary keys `(tenant_id, id)` (`tenant` itself: `tenant_id`), ids are UUIDv7. Timestamps `timestamptz` UTC.
- Every FK is composite `(tenant_id, x_id) → parent(tenant_id, id)`; venue-scoped tables carry `venue_id`.
- Indexes start with `tenant_id`. Money as `numeric(12,2)` with currency column (AUD default).
  Exception: `usage_cost.cost` is `numeric(14,6)` (per-call model cost is sub-cent); round when reporting.
- `chunk.embedding` is `vector` with no dimension and no HNSW index until the embedding model is chosen
  (ADR then; open-questions.md).

## Isolation mechanics (P0-03)

- RLS enabled **and forced** on every table; policy `tenant_isolation`:
  `tenant_id = va_current_tenant()` for USING and WITH CHECK. `va_current_tenant()` reads the
  transaction-local `app.tenant_id`; unset → NULL → no rows, every write rejected (fail closed).
- `tenant_id` defaults to `va_current_tenant()`, so code never passes it.
- Roles: the migration/admin login owns the schema (locally a superuser, so it bypasses RLS — never used
  by runtime code). Runtime logins are members of `va_app` (NOLOGIN, NOBYPASSRLS, owns nothing).
  `tenant` is SELECT-only (lifecycle is the control plane's); `tool_call`, `approval`, `usage_cost` are
  append-only; `action_log` has no DELETE. The admin URL lives only in `va_db.settings.MigrationSettings`.
- Code uses `va_db.session.tenant_session(sessionmaker, ctx)` on an engine from `create_engine` /
  `create_runtime_engine`: the tenant is set at the start of every transaction, connections `RESET ALL`
  on return to the pool, and a statement guard rejects anything else touching `app.tenant_id`,
  `set_config`, roles, `search_path`, `RESET` or `DISCARD`. Services must call
  `assert_rls_enforced(engine)` at start-up (refuses logins that can reach a superuser/BYPASSRLS role,
  own tables, or aren't `va_app` members).
- `va_current_tenant()` is `BEGIN ATOMIC` with schema-qualified names (bound at creation).
- Follow-ups before P0-08/P0-09: per-service roles (H1) and approval-to-content binding (H2); see the
  phase-0 task list.
