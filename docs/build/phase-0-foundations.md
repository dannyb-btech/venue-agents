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
    (2026-10-25, after pinning setup-uv v10.3.2); compose verified 2026-10-25 (DTS emulator pinned v0.0.9).

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

- [x] **P0-03 Stamp database + RLS** — deps: P0-02 — docs: data-model.md, ADR-0007, ADR-0008
  - `packages/db`: SQLAlchemy models + Alembic for platform tables (tenant, venue, app_user,
    role_assignment, connection, agent_config) and agent-activity tables (agent_run, tool_call, draft,
    approval, action_log, usage_cost), `sync_cursor`, `document`/`chunk` (pgvector).
    `(tenant_id, id)` keys, tenant_id in all FKs, RLS on `app.tenant_id`, app role without BYPASSRLS.
    Session helper that sets the tenant from `TenantContext`.
  - Accept: `tests/isolation/` proves tenant A can't read/write tenant B for every table, and queries fail
    closed without the tenant set; migrations upgrade/downgrade cleanly.
  - Done 2026-10-25: 15 tables in migrations 0001–0005 (forced RLS, composite keys/FKs, `va_app` role,
    append-only audit tables, `tenant` SELECT-only); `tenant_session` + hardened runtime engine (pool
    `RESET ALL`, statement guard, `assert_rls_enforced`). 143 isolation tests (per-table behaviour +
    catalog meta-tests) and migration round-trip/model-drift tests. Security review H1/H2 scheduled on P0-08/P0-09.

- [x] **P0-04 Control plane + tenant catalog** — deps: P0-03 — docs: scale-plan.md, ADR-0008
  - `apps/control-plane`: catalog DB (venue_directory, stamp, entitlement, onboarding_job), `GET /route`,
    `POST /venues` (placement on a stamp + create tenant in the stamp DB), entitlements read API.
  - Accept: onboarding a venue creates catalog + stamp records; routing returns the right stamp; tests
    run with two local stamps (two DBs) to avoid single-stamp assumptions.
  - Done 2026-10-27 (ADR-0017): catalog schema + own Alembic tree (`va_catalog`, no RLS), stamp migration
    0006 `va_provision`, placement (tenant's stamp / least-loaded, advisory lock), onboarding job with
    retry + Idempotency-Key, FastAPI API + `register-stamp`/`serve` CLI, Entra password-provider seam with
    deployed TLS checks. Tests on one catalog + two stamp DBs; security + architect reviews addressed.
  - Edge routing and entitlement distribution: decided in ADR-0019 (P0-05).

- [x] **P0-05 Platform API skeleton** — deps: P0-03 — docs: api.md
  - FastAPI app: auth (Entra External ID token validation; dev-mode stub issuer for local), role checks,
    `/me`, `/connections` + OAuth start/callback scaffolding, `/drafts`, approve/reject (raising an
    approval event onto Service Bus), `/runs`. OpenAPI published.
  - Accept: API tests incl. a viewer can't approve; no endpoint accepts tenant_id; OpenAPI generated.
  - **ADR-0018:** OAuth callback stores tokens as `venue_secret_name(ctx, system, "refresh-token")` on
    the venue's `connection` row and refreshes only through `va_core.credentials.TenantCredentials`.
  - Done 2026-10-28 (ADR-0019):
    - **API** (`va_platform_api`): Entra External ID token validation (dev HS256 issuer locally);
      tenant from the token claim; user and roles from the stamp DB; `X-Venue-Id` authorised per
      request.
    - **Endpoints:** `/me`, the drafts inbox, approve/reject/re-publish (bound to `payload_sha256`,
      `approval.decided` events), connections plus OAuth scaffolding (hashed single-use state,
      derived PKCE verifier), runs. `openapi.json` committed.
    - **Database:** migrations 0007–0010 (approval hash, draft guard, approval-required trigger,
      `oauth_state`, `va_provision` user grants).
    - **Control plane:** `POST /venues/{id}/users`.
    - **Telemetry:** URL-free request tracing (P0-02 M1) and the stamp check (L2).
    - **Reviews:** security and architect findings were fixed or recorded below.

- [x] **P0-06 Connector framework** — deps: P0-02 — docs: integrations.md, ADR-0005
  - `packages/core.mcp`: MCP server base with tenant-context middleware, tool registry enforcing
    read/draft categories, draft helper writing `draft` rows, per-tenant credential loading, rate limiting.
  - Accept: a sample connector with one read and one draft tool passes tests; a tool marked "write" is
    rejected at registration.
  - Done 2026-10-28 (ADR-0018): `va_core.mcp` (`va-core[mcp]`, mcp 2.3.0): HS256 per-stamp run envelopes,
    `ToolRegistry` (read/draft only, `<system>_<verb>_<noun>`, no tenant/alias args), stateless MCP server
    with envelope middleware (method + tool allow-list, raw-argument check, rate limits, safe errors, spans),
    `ToolContext` bound to the envelope; `va_core.credentials` (per-venue, refresh serialised per credential,
    rotation stored in the lock); `va_db.connectors` + `connector_deps`. Sample connector tests incl. a real
    uvicorn socket; isolation tests (drafts, two venues, cross-tenant secret refs, refresh lock).

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
  - **Prereq (P0-03 security review H1):** per-service DB roles (`va_api`, `va_agent`, `va_executor`,
    `va_sync`, `va_connector`) replacing the shared `va_app`, with an ADR and a per-role privilege-matrix
    test. The agent runtime must not be able to write `approval`, `role_assignment` or `action_log`;
    `va_connector` gets SELECT on `connection`/`agent_run` and INSERT on `draft` only (ADR-0018).
  - **Prereqs (ADR-0018):** mint run envelopes per MCP request (check Agent Framework's MCP tool header
    hook with `azure-researcher`); `agent_run` row before the first envelope; draft idempotency key
    (unique `(tenant_id, run_id, idempotency_key)`) so retried activities don't duplicate drafts;
    decide how the Service Bus trigger message is trusted; the runtime's MCP client must use the
    handshake-era protocol against connectors until mcp fixes the stateless 2026-07-28 path (lessons.md).
  - **Prereqs (ADR-0019, P0-05 reviews):**
    - **Role matrix (H1):** give the Platform API's role write on `app_user`, `role_assignment`,
      `approval`, `oauth_state` and `connection` upsert; the runtime and connectors get no write on
      those.
    - **Event trust:** decide how `approval.decided` consumers trust the event's tenant, in the
      trigger-trust ADR (Send RBAC + re-read under RLS, or a signed envelope).
    - **Waiting runs:** add the `runtime` subscription that raises the orchestration's external
      event.

- [ ] **P0-09 Action executor** — deps: P0-08 — docs: ADR-0005, ADR-0006
  - Consumes approval events; dispatches by draft type to handlers; idempotent on approval_id;
    writes action_log; first handler: Gmail send-draft (sandbox only).
  - Accept: duplicate events don't double-send; missing approval → refused and logged.
  - **Prereq (P0-03 security review H2):** bind each approval to the content approved
    (`approval.payload_sha256` from the payload the API showed; executor recomputes and refuses on
    mismatch) and make `draft` immutable once it leaves `pending` (trigger + allowed status transitions).
  - **ADR-0018:** write-scoped grants use their own connection secret kind and refresh-lock key.
  - **ADR-0019 / P0-05 reviews:**
    - **Consumption:** consume `approval.decided` (subscription `executor`) and act on `approved`
      only.
    - **Checks before acting:** re-read the approval and draft under RLS; check `payload_sha256` and
      that the draft's venue equals the event's.
    - **Approval age:** enforce a maximum approval age per draft type.
    - **Reconciler:** add one for approved drafts with no `action_log`. Its tenant list comes from
      the catalog or a recorded exception, and it alerts on `event_published=false`.
    - **Write grants:** OAuth start/callback get a grant `kind` for write-scoped grants.

- [ ] **P0-10 Approvals inbox v0** — deps: P0-05 — rules: frontend.md
  - `apps/venue-app`: Next.js PWA, generated API client, sign-in, pending drafts list, draft detail with
    reasoning + sources, approve/edit/reject.
  - Accept: Playwright test of approve and reject flows; usable at phone width.
  - **From P0-05:**
    - CORS allow-list for the local venue app; same-origin through APIM in Azure.
    - A cursor for `/drafts` and `/runs` before the client is generated.
    - Register an editable draft type, or scope out "edit".
    - Provider redirects land on `/connections/callback`, which posts `{code, state}`.

- [ ] **P0-11 Infrastructure as code** — deps: P0-01 — docs: ADR-0010, rules: infra.md
  - Bicep modules: `global/` (Front Door, API Management with AI gateway policies placeholder, catalog
    Postgres, App Configuration, Foundry connection references) and `stamp/` (Container Apps env + apps,
    Postgres flexible + pgvector, Key Vault, Service Bus, Blob, Durable Task Scheduler, App Insights,
    managed identities, private endpoints). Params: `canary.bicepparam`, `general-01.bicepparam`.
    GitHub Actions workflow for what-if + deploy with manual approval (not run by Claude).
  - Accept: `az bicep build` and `az bicep lint` clean; a stamp is fully described by its params file.
  - **From P0-05 (ADR-0019):**
    - `approvals` topic with duplicate detection (set at creation) and subscriptions `executor` and
      `runtime`.
    - Send rights for the Platform API identity only.
    - A set-only Key Vault role for the API (plus a read of `platform-oauth-state-key`).
    - Start containers with `python -m va_platform_api serve` (access log off).
    - Entra External ID app registration (`accessTokenAcceptedVersion: 2`, `access_as_user` scope).
      The tenant attribute must not be in sign-up or profile edit.

- [ ] **P0-12 Feature flags, entitlements, rings** — deps: P0-04 — docs: scale-plan.md
  - App Configuration feature flags read via `packages/core`; entitlements from catalog; ring per stamp.
  - Accept: a flag can enable an agent for one venue only; tests cover the evaluation logic.
  - **From ADR-0017/0019:**
    - Control-plane Entra service tokens, with app roles mapped to scopes.
    - A `stamp_id` claim, with `/route` and `/entitlements` filtered by it.
    - Stamp services cache entitlements with a TTL.
    - A dedicated `users:write` scope with an audit row for role grants (security review L4).

- [ ] **P0-13 External access requests (human)** — deps: none — docs: ADR-0011
  - Toast: confirm OR's RMS tier and whether standard API access is available in AU; else request SFTP exports.
  - SevenRooms: submit partnership form for API credentials.
  - Google: create Cloud project, OAuth consent (internal for OR's workspace).
  - Xero: create developer app; check pricing tier. Tanda: create API app/token.
  - Foundry: Claude deployments (Haiku, Sonnet, Opus) in a dev Foundry resource; record quotas.
  - Entra External ID: create the external tenant; then confirm the tenant custom attribute reaches
    the Platform API's access tokens (ADR-0019, open-questions.md).
  - Accept: each item has an owner, date requested, status in `docs/memory/open-questions.md`.

- [ ] **P0-14 Gate 1 check** — deps: P0-07, P0-11 (deployed by human) — OR read-only data syncing into the
  canary stamp; isolation tests green in CI; runbooks drafted for token expiry and sync failure.
