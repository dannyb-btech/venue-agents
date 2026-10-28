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
| Secrets | Key Vault | OAuth refresh tokens and API keys per venue connection (ADR-0018) |
| Workflow state | Durable Task Scheduler | Orchestration checkpoints, approval waits |
| Messaging | Service Bus | Webhooks, triggers, sync jobs, `approval.decided` events (`approvals` topic) |
| Catalog | Postgres (global) | Venue → stamp, region, plan, entitlements |
| Analytics | Central store (Fabric or replica) | Cross-venue reporting; no app query crosses venues |

## Schema groups (stamp DB)

**Platform:** `tenant`, `venue`, `app_user`, `role_assignment`, `connection` (system, status, scopes,
secret_ref), `agent_config` (targets e.g. labour %, wine GP, approval rules, schedules).

**Synced business data:** `guest`, `reservation`, `event_enquiry`, `sale_line`, `menu_item`,
`stock_level`, `supplier`, `price_list_item`, `purchase_order`, `employee`, `shift`, `timesheet`,
`pay_run_summary`. Each has `source_system`, `source_id`, `synced_at`, unique on
`(tenant_id, source_system, source_id)`. Payroll kept summarised unless a task needs line level.

**Connections:** `oauth_state` (venue, user, system, hash of a single-use state, expiry, used_at):
an OAuth connect flow in progress (ADR-0019).

**Sync state:** `sync_cursor` (tenant, system, stream, cursor, last_success_at, last_error).

**Agent activity:** `agent_run` (agent, prompt_version, model, status, started/ended, cost),
`tool_call`, `draft` (type, payload JSON, status, reasoning summary, sources; frozen once decided by
the `va_draft_guard` trigger), `approval` (user, decision, edits, reason, `payload_sha256` of what
will be executed, at), `action_log` (external_id, result, at), `usage_cost`
(tokens in/out/cached, cost, venue_id).

**Knowledge:** `document` (source, title, blob_ref), `chunk` (text, embedding vector, document_id).

## Catalog DB (global, ADR-0017)

`stamp` (id, region, ring, capacity, status, non-secret connection details: db host/port/name,
provision login, sslmode), `tenant_directory` (tenant_id → stamp_id, status),
`venue_directory` (tenant_id, venue_id, stamp_id, region, plan, status; FK `(tenant_id, stamp_id)` →
tenant_directory so a venue can't leave its tenant's stamp), `entitlement` (tenant_id, venue_id,
entitlement, revoked_at), `onboarding_job` (status, attempts, error_code, idempotency_key, the
provisioning inputs). `move_job`
comes with the move-venue tool (phase 3).

- No RLS: the catalog is cross-tenant by design. Only `va_catalog` members reach it: SELECT on
  `stamp` (operators register stamps with the catalog owner login via the CLI), SELECT/INSERT/UPDATE
  on the rest, no DELETE. No people or business data; the only names are onboarding inputs.
- Venue status `provisioning` → `active` (→ `suspended` / `offboarding` / `offboarded`); only
  `active` and `suspended` venues route.
- Separate Alembic tree: `packages/db/alembic-catalog.ini` (`CATALOG_ADMIN_URL`), revisions `c0001`…

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
  `tenant` is SELECT-only for `va_app` (lifecycle is the control plane's, via `va_provision`); `tool_call`, `approval`, `usage_cost` are
  append-only; `action_log` has no DELETE. The admin URL lives only in `va_db.settings.MigrationSettings`.
- Code uses `va_db.session.tenant_session(sessionmaker, ctx)` on an engine from `create_engine` /
  `create_runtime_engine`: the tenant is set at the start of every transaction, connections `RESET ALL`
  on return to the pool, and a statement guard rejects anything else touching `app.tenant_id`,
  `set_config`, roles, `search_path`, `RESET` or `DISCARD`. Services must call
  `assert_rls_enforced(engine)` at start-up (refuses logins that can reach a superuser/BYPASSRLS role,
  own tables, or aren't members of exactly the expected group role).
- `va_current_tenant()` is `BEGIN ATOMIC` with schema-qualified names (bound at creation).
- `va_provision` (P0-04, ADR-0017): the control plane's stamp role, SELECT+INSERT on `tenant` and
  `venue` only, still under RLS (it sets the new tenant with `tenant_session`). `assert_rls_enforced`
  takes the expected role. In Azure every login is a managed identity (Entra token as the password,
  via the runtime engine's password provider) mapped to a member of the group role.
- `va_provision` also has SELECT+INSERT on `app_user` and `role_assignment` (0009): operators add venue
  users through the control plane (ADR-0019).
- Approval binding (0007, review H2): `approval.payload_sha256` is required for approvals; the
  `va_draft_guard` trigger keeps draft identity columns fixed, freezes payload/reasoning/sources once
  the draft leaves `pending`, allows only pending → approved/rejected/superseded and
  approved → executed/failed, and refuses deleting decided drafts.
- Follow-up before P0-08: per-service roles (H1); see the phase-0 task list.
- Check constraints created before 0007 carry a doubled prefix (`ck_draft_ck_draft_status`): the
  migrations passed full names through the naming convention. Leave them; new ones use `op.f()`.
