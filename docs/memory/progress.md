# Progress

## Current state

- **Phase:** 0 — Foundations (in progress)
- **Last task done:** P0-06 Connector framework (ADR-0018; committed on `dev`, not pushed)
- **Next task:** P0-05 Platform API (P0-11 also unblocked; P0-07 waits on P0-13 human access requests).
  Before P0-05/P0-12: ADR on edge routing + entitlement distribution (ADR-0017 follow-up). Before
  P0-08: H1 per-service roles (now incl. `va_connector`), envelope minting per MCP request, draft
  idempotency key, MCP client on the handshake-era protocol (see phase file). Before P0-09: H2.
- **Blockers:** Toast access route for OR unknown; SevenRooms credentials not yet requested;
  no Azure subscription for this project yet (local development only until then)
- **Local services:** `docker compose up -d` — Postgres (pgvector 0.8.7; logins `va_app_local`,
  `va_catalog_local`, `va_provision_local`), Azurite, DTS emulator v0.0.9 (dashboard http://localhost:8082).
  DB tests need `DATABASE_ADMIN_URL` (127.0.0.1) in `.env`. Catalog: `CATALOG_ADMIN_URL`.
- **CI:** GitHub repo `dannyb-btech/venue-agents`; green on `dev` at P0-03; P0-04 and P0-06 not yet pushed
- **Environment:** Windows, VS Code + Claude Code, uv 0.13.5 (Python 3.12.15), pnpm, Docker Desktop.
  Norton Web Shield intercepts HTTPS: see lessons.md
- **Branches:** `main` (scaffold) and `dev` (work); humans merge to `main` and push

## Session log (newest first)

### 2026-10-28 — P0-06 Connector framework
- Decided with user (ADR-0018): run envelopes are HS256 JWTs with a per-stamp key (`X-VA-Run-Envelope`,
  ≤ 600 s, audience = one connector, carrying the agent's tool allow-list); tool names
  `<system>_<verb>_<noun>`, draft tools `<system>_draft_<noun>`; mcp 2.3.0 (`va-core[mcp]`, PyJWT 2.15.1).
- `va_core.mcp`: envelope, `ToolRegistry` (read/draft only, no tenant/alias args), stateless MCP server
  (method + tool allow-list, raw-argument check, rate limits, code-only errors, spans), `ToolContext`
  bound to the envelope, sample connector + fakes (`testing.py`). `va_core.credentials` (per-venue,
  refresh per credential with rotation stored inside the lock), `va_core.tools`, `venue_secret_name`;
  `va_db.connectors` (draft writer checks the run's venue/status, try-lock with bounded wait),
  `va_db.connector_deps`. Ruff `SLF` enabled. Closes P0-02 follow-ups M6 and OAuth refresh serialisation.
- Verified: ruff, format, pyright 0 errors; pytest 490 passed / 4 expected skips; isolation 169 passed; CI non-DB subset 293 passed;
  7 mutation checks caught (write category, allow-list, stamp, per-tenant credential key, method
  allow-list, unknown args, draft venue); manual run over a real uvicorn socket.
- Found: mcp 2.3.0 stateless JSON responses arrive empty under uvicorn and the 2026-07-28 protocol path
  fails there (ASGI test transport hides both): switched to SSE responses, added a real-socket test.
- security-reviewer (H1 per-tenant token cache across venues; M1-M4; L1-L5) and architect-reviewer
  (12 findings) addressed in code or recorded as P0-05/P0-08/P0-09/P0-11 follow-ups in ADR-0018 and the
  phase file (draft idempotency key, `va_connector` role, Key Vault set rights, envelope minting).

### 2026-10-26/27 — P0-04 Control plane + tenant catalog
- Decided with user (ADR-0017): catalog without RLS behind `va_catalog`; `va_provision` stamp role
  (SELECT+INSERT tenant/venue, under RLS); local dev-token auth until P0-05; tenant's stamp else least-loaded;
  Entra managed-identity DB logins via a connect-time password provider (azure-researcher checked Learn).
- `va_db`: catalog models + Alembic tree (`alembic-catalog.ini`, `c0001`), stamp migration 0006,
  `credentials.py` (Entra/secret providers, `check_deployed_tls`), `assert_rls_enforced(engine, role)` with
  one-group-role check, `hide_parameters`, `temp_database(..., logins=)` + `temp_catalog`; root `conftest.py`
  holds the Windows selector-loop hook. `CoreSettings` refuses local/test on Azure hosting.
- `apps/control-plane`: placement, onboarding job (in-process retry, stale-job fencing, Idempotency-Key),
  provisioner, FastAPI `/route`, `/venues`, retry, entitlements, `/stamps`; CLI `register-stamp`, `serve`.
- Verified: ruff, format, pyright 0 errors, pytest 355 passed / 4 expected skips, isolation 160 passed,
  non-DB CI subset 167 passed, gitleaks; mutation checks (tenant-stamp placement, routable statuses) bite;
  manual run on local compose (onboard, route, entitlements, replay; logs carry venue only).
- security-reviewer (no High) and architect-reviewer findings fixed or recorded in ADR-0017 follow-ups
  (edge routing/entitlements ADR, P0-11 Entra/TLS/private networking checks, move-tenant, dedicated stamps).

### 2026-10-25 — P0-03 Stamp database + RLS
- `va_db`: 15 tables (models + hand-written Alembic 0001–0005), composite `(tenant_id, id)` keys/FKs,
  forced RLS via `va_current_tenant()` (BEGIN ATOMIC, fail closed), `va_app` NOLOGIN/NOBYPASSRLS group
  role; audit tables append-only, `tenant` SELECT-only. Decided with user: `chunk.embedding` dimensionless
  (no HNSW until embedding model + ADR); `usage_cost.cost` numeric(14,6).
- `tenant_session` (tenant re-set per transaction via `after_begin`), runtime engine with pool
  `RESET ALL`, statement guard, connect timeout; `assert_rls_enforced` (not wired yet — call it at
  start-up in P0-04/P0-05). Admin URL moved to `va_db.settings.MigrationSettings`.
- Verified: ruff, format, pyright strict 0 errors, pytest 253 passed / 4 expected skips, isolation 143
  passed, local alembic up/down/up, gitleaks; mutation checks confirmed the reset, guard and FORCE-RLS tests bite.
- security-reviewer: fixed M1–M5, L1–L5; H1 (per-service roles) and H2 (approval payload hash + draft
  immutability) scheduled as P0-08/P0-09 prerequisites in the phase file. CI green on GitHub (all 5 jobs).

### 2026-10-25 — P0-02 Core package
- `va_core`: `TenantContext` + `tenant_scope`/`run_scope` (contextvars; fail closed; any context change
  inside a scope raises), `CoreSettings` (ENVIRONMENT required, `env_ignore_empty`), typed errors,
  redaction (TFN/Luhn checksums, bank, tokens, JSON/camelCase keys, emails masked to `j***@domain`),
  telemetry (`TenantSpanProcessor`, `TelemetryLogFilter` on handlers, Azure Monitor distro only with a
  connection string), secrets (Key Vault + TTL cache except `*-token`, `.env` store, `TenantSecrets`),
  `HttpClient` (retries only GET/HEAD/OPTIONS, `idempotent=True`, or supported idempotency keys;
  `outcome_unknown` on possibly-sent writes).
- Verified: ruff, format, pyright strict (0 errors), pytest 96 passed, isolation 1 passed.
- security-reviewer: fixed H1–H3 (double-send risks, unredacted `extra=`) and M2–M5, L1, L3–L5, L7.
- Follow-ups (not done): **M1** redact span attributes/exception events before export (FastAPI
  `http.url` query, exception messages via `exc_info`) — before P0-05; **M6** give connectors only
  `TenantSecrets`, keep the raw store in bootstrap, add a cross-tenant secret isolation test — P0-06;
  **L2** edge resolver rejects contexts for another stamp — P0-05; serialise OAuth token refresh per
  tenant+system — P0-06.

### 2026-10-25 — Orientation + P0-01
- Orientation: read docs/ADRs; confirmed Agent Framework packages (core 1.23.0 GA; anthropic
  1.0.0b261022 and durabletask 1.0.0b260922 prerelease) via azure-researcher (PyPI + Learn).
- P0-01: git (`main` + `dev`), uv workspace with 7 `va-*` members, ruff/pyright (strict on packages)/
  pytest/pre-commit (ruff local hooks + gitleaks), docker-compose (Service Bus opt-in profile), CI workflow.
- ADR-0016: direct Foundry calls allowed only when `ENVIRONMENT=local`.
- Verified locally: ruff, ruff format, pyright (0 errors), pytest (8 passed), isolation (1 placeholder),
  `docker compose config` for both profiles. Not verified: CI (not pushed), containers (Docker off).
- Follow-ups: ADR-0004 revisit trigger (Agent Framework GA) to review before P0-08.

### 2026-10-23 — Scaffold
- Created repo structure, CLAUDE.md, `.claude/` rules, skills, subagents, settings, `.mcp.json`.
- Recorded ADRs 0001–0015 from the build plan and architecture discussions.
- Wrote architecture docs, phase task lists (P0–P3) and session prompts.
- Verification: none yet (no code).
