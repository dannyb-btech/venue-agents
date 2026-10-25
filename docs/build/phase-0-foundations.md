# Phase 0 — Foundations (weeks 1–3)

Goal: everything a single agent run needs end to end, with tenancy and scale foundations built in.
Gate 1: OR data flowing (read-only) into the canary stamp.

- [x] **P0-01 Monorepo bootstrap** — deps: none — docs: CLAUDE.md, ADR-0009
  - `uv` workspace (Python 3.12) with members for each app and package; ruff, pyright, pytest,
    pytest-asyncio; pre-commit; `docker-compose.yml` (Postgres 16 + pgvector, Azurite, Service Bus
    emulator, Durable Task Scheduler emulator); GitHub Actions CI (lint, types, tests, isolation, secret scan).
  - Accept: `uv sync`, `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass on a fresh clone;
    CI green; CLAUDE.md commands section confirmed accurate.
  - Done 2026-10-25: uv workspace of 7 `va-*` members, ruff/pyright/pytest/pre-commit (gitleaks), compose
    (Service Bus opt-in profile), CI workflow. Checks pass locally and on a fresh clone; CI green on GitHub
    (2026-10-25, after pinning setup-uv v10.3.2); compose not started (Docker Desktop was off).

- [x] **P0-02 Core package: tenancy, settings, telemetry, secrets, errors** — deps: P0-01 — docs: security.md
  - `packages/core`: `TenantContext` (tenant_id, venue_id, stamp_id, user/role), context propagation,
    pydantic-settings, OpenTelemetry setup with venue_id/run_id on every span and log, redaction helpers,
    secrets interface (Key Vault impl + local `.env` impl), typed errors, HTTP client with
    timeouts/retries/Retry-After.
  - Accept: unit tests for context propagation, redaction, retry behaviour; no `os.environ` outside settings.
  - Done 2026-10-25: `va_core` tenancy (contextvars, fail-closed, no context change inside a scope), settings
    (ENVIRONMENT required), redaction + handler log filter, OTel/Azure Monitor setup, Key Vault/.env secrets
    with `TenantSecrets`, typed errors, retrying HTTP client (no blind re-send; `outcome_unknown`). 96 tests
    incl. an `os.environ` guard; security review findings H1–H3 fixed, rest tracked in progress.md.

- [ ] **P0-03 Stamp database + RLS** — deps: P0-02 — docs: data-model.md, ADR-0007, ADR-0008
  - `packages/db`: SQLAlchemy models + Alembic for platform tables (tenant, venue, app_user,
    role_assignment, connection, agent_config) and agent-activity tables (agent_run, tool_call, draft,
    approval, action_log, usage_cost), `sync_cursor`, `document`/`chunk` (pgvector).
    `(tenant_id, id)` keys, tenant_id in all FKs, RLS on `app.tenant_id`, app role without BYPASSRLS.
    Session helper that sets the tenant from `TenantContext`.
  - Accept: `tests/isolation/` proves tenant A can't read/write tenant B for every table, and queries fail
    closed without the tenant set; migrations upgrade/downgrade cleanly.

- [ ] **P0-04 Control plane + tenant catalog** — deps: P0-03 — docs: scale-plan.md, ADR-0008
  - `apps/control-plane`: catalog DB (venue_directory, stamp, entitlement, onboarding_job), `GET /route`,
    `POST /venues` (placement on a stamp + create tenant in the stamp DB), entitlements read API.
  - Accept: onboarding a venue creates catalog + stamp records; routing returns the right stamp; tests
    run with two local stamps (two DBs) to avoid single-stamp assumptions.

- [ ] **P0-05 Platform API skeleton** — deps: P0-03 — docs: api.md
  - FastAPI app: auth (Entra External ID token validation; dev-mode stub issuer for local), role checks,
    `/me`, `/connections` + OAuth start/callback scaffolding, `/drafts`, approve/reject (raising an
    approval event onto Service Bus), `/runs`. OpenAPI published.
  - Accept: API tests incl. a viewer can't approve; no endpoint accepts tenant_id; OpenAPI generated.

- [ ] **P0-06 Connector framework** — deps: P0-02 — docs: integrations.md, ADR-0005
  - `packages/core.mcp`: MCP server base with tenant-context middleware, tool registry enforcing
    read/draft categories, draft helper writing `draft` rows, per-tenant credential loading, rate limiting.
  - Accept: a sample connector with one read and one draft tool passes tests; a tool marked "write" is
    rejected at registration.

- [ ] **P0-07 First connectors: Gmail, Drive, Tanda, Xero (read)** — deps: P0-06, P0-13 — skill: `/new-connector`
  - Read tools + sync workers for each, against sandbox/demo orgs, recorded anonymised fixtures.
  - Accept: per-connector tests and isolation tests pass; integrations.md rows updated.

- [ ] **P0-08 Agent runtime skeleton** — deps: P0-05, P0-06 — docs: agents.md, ADR-0004 — use `azure-researcher` first
  - Agent Framework (pinned prerelease versions) with the Anthropic-on-Foundry client via the model
    client in `packages/core` (gateway URL configurable); durable extension with the DTS emulator;
    a `hello` agent that reads a connector, creates a draft, waits for approval, then completes.
    Run logging: agent_run, tool_call, usage_cost.
  - Accept: end-to-end local test (with a mocked model client) passes; one manual run against a Foundry dev
    deployment documented in lessons.md; orchestration survives a worker restart mid-wait.

- [ ] **P0-09 Action executor** — deps: P0-08 — docs: ADR-0005, ADR-0006
  - Consumes approval events; dispatches by draft type to handlers; idempotent on approval_id;
    writes action_log; first handler: Gmail send-draft (sandbox only).
  - Accept: duplicate events don't double-send; missing approval → refused and logged.

- [ ] **P0-10 Approvals inbox v0** — deps: P0-05 — rules: frontend.md
  - `apps/venue-app`: Next.js PWA, generated API client, sign-in, pending drafts list, draft detail with
    reasoning + sources, approve/edit/reject.
  - Accept: Playwright test of approve and reject flows; usable at phone width.

- [ ] **P0-11 Infrastructure as code** — deps: P0-01 — docs: ADR-0010, rules: infra.md
  - Bicep modules: `global/` (Front Door, API Management with AI gateway policies placeholder, catalog
    Postgres, App Configuration, Foundry connection references) and `stamp/` (Container Apps env + apps,
    Postgres flexible + pgvector, Key Vault, Service Bus, Blob, Durable Task Scheduler, App Insights,
    managed identities, private endpoints). Params: `canary.bicepparam`, `general-01.bicepparam`.
    GitHub Actions workflow for what-if + deploy with manual approval (not run by Claude).
  - Accept: `az bicep build` and `az bicep lint` clean; a stamp is fully described by its params file.

- [ ] **P0-12 Feature flags, entitlements, rings** — deps: P0-04 — docs: scale-plan.md
  - App Configuration feature flags read via `packages/core`; entitlements from catalog; ring per stamp.
  - Accept: a flag can enable an agent for one venue only; tests cover the evaluation logic.

- [ ] **P0-13 External access requests (human)** — deps: none — docs: ADR-0011
  - Toast: confirm OR's RMS tier and whether standard API access is available in AU; else request SFTP exports.
  - SevenRooms: submit partnership form for API credentials.
  - Google: create Cloud project, OAuth consent (internal for OR's workspace).
  - Xero: create developer app; check pricing tier. Tanda: create API app/token.
  - Foundry: Claude deployments (Haiku, Sonnet, Opus) in a dev Foundry resource; record quotas.
  - Accept: each item has an owner, date requested, status in `docs/memory/open-questions.md`.

- [ ] **P0-14 Gate 1 check** — deps: P0-07, P0-11 (deployed by human) — OR read-only data syncing into the
  canary stamp; isolation tests green in CI; runbooks drafted for token expiry and sync failure.
