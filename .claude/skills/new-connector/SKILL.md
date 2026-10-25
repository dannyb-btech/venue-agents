---
description: Scaffold a new external-system connector (MCP server with read and draft tools, sync worker, executor actions, fixtures, docs). Use when adding an integration such as SevenRooms, Toast, Tanda, Xero, Stripe, Webflow or Meta.
disable-model-invocation: true
argument-hint: <system-name>
---

# New connector: $0

1. Read `docs/architecture/integrations.md` (access route, scopes, gotchas for $0) and
   `.claude/rules/connectors.md`. Ask `azure-researcher` or check vendor docs for current API details;
   record the doc URLs in the integrations doc.
2. Create `apps/connectors/$0/` from the shared base in `packages/core.mcp`:
   - `client.py` — typed async API client (timeouts, retries, `Retry-After`, per-tenant credentials).
   - `models.py` — Pydantic models for the vendor objects we use and our normalised shapes.
   - `tools.py` — read tools and draft tools only, named `$0.<verb>_<noun>`.
   - `server.py` — MCP server registering the tools with tenant-context middleware.
3. Sync: add `apps/sync-workers/.../$0.py` with an incremental cursor and idempotent upserts into the
   synced tables (add a migration with RLS + isolation test if a new table is needed).
4. Actions: if the system needs writes, add executor handlers in `apps/action-executor/` keyed by
   draft type, with idempotency on `approval_id`.
5. Tests: recorded, anonymised fixtures in `tests/fixtures/$0/`; tests for each tool, sync cursor
   handling, 429 backoff, and a cross-tenant isolation test.
6. Docs: update the connector row in `docs/architecture/integrations.md` (scopes, rate limits,
   sync cadence, write paths). Run `/verify` and the `security-reviewer` subagent.
