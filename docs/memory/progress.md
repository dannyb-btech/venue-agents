# Progress

## Current state

- **Phase:** 0 — Foundations (in progress)
- **Last task done:** P0-03 Stamp database + RLS (commit on `dev`; not pushed)
- **Next task:** P0-04 Control plane + tenant catalog (P0-05, P0-06, P0-11 also unblocked; P0-13 human).
  Before P0-08/P0-09: security-review H1 (per-service DB roles + ADR) and H2 (approval bound to payload).
- **Blockers:** Toast access route for OR unknown; SevenRooms credentials not yet requested;
  no Azure subscription for this project yet (local development only until then)
- **Local services:** `docker compose up -d` — Postgres (pgvector 0.8.7; `va_app` + `va_app_local` from
  init.sql, volume recreated 2026-10-25), Azurite, DTS emulator v0.0.9 (dashboard http://localhost:8082).
  DB tests need `DATABASE_ADMIN_URL` (127.0.0.1, not localhost) in `.env`.
- **CI:** GitHub repo `dannyb-btech/venue-agents`; CI green on `dev` (all 5 jobs) as of 2026-10-25
- **Environment:** Windows, VS Code + Claude Code, uv 0.13.5 (Python 3.12.15), pnpm, Docker Desktop.
  Norton Web Shield intercepts HTTPS: see lessons.md
- **Branches:** `main` (scaffold) and `dev` (work); humans merge to `main` and push

## Session log (newest first)

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
  immutability) scheduled as P0-08/P0-09 prerequisites in the phase file. CI DB job not yet run on GitHub.

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
