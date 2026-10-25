# Lessons

Things that cost time: library quirks, API gotchas, environment issues. Newest first.
Format: date — area — what happened — what to do instead.

- 2026-10-25 — Postgres on Windows — `localhost:5432` hung forever in psycopg (`alembic upgrade` sat in
  `wait_conn`): `localhost` tries `::1` first and compose publishes on `127.0.0.1` only; with a
  connect timeout it falls back after the timeout. Use `127.0.0.1` in URLs and always set
  `connect_timeout` (`DATABASE_CONNECT_TIMEOUT`).
- 2026-10-25 — psycopg async on Windows — needs a selector event loop, not the default Proactor.
  pytest-asyncio 1.4 deprecates overriding `event_loop_policy`; implement the
  `pytest_asyncio_loop_factories(config, item)` hook (returns `{name: factory}`), see tests/isolation/conftest.py.
- 2026-10-25 — RLS — Postgres checks the RLS WITH CHECK before NOT NULL, so an insert with no tenant
  fails with "violates row-level security policy", not "null value". Superusers (the compose/CI `app`
  login) bypass RLS even with FORCE: never run app code or RLS assertions as the admin login.
- 2026-10-25 — Postgres GUCs — a session-level `SET`/`set_config(..., false)` made in a transaction that is
  rolled back is reverted too. A "leak through the pool" test must commit the setting first, or it passes
  without any reset. Also: psycopg (non-autocommit) opens a transaction for `RESET ALL` in a pool `reset`
  hook — commit afterwards or the next checkout fails with "connection in transaction status INTRANS".
- 2026-10-25 — SQLAlchemy 2.1 — `commit()` inside `async with session.begin():` raises
  "Can't operate on closed transaction inside context manager". `tenant_session` manages commit/rollback
  itself and re-sets the tenant in an `after_begin` listener so mid-block commits are safe.

- 2026-10-25 — Docker Compose — DTS emulator `v0.0.10` (latest) crashes on start with "Scheduler name is not
  set", even with the documented `docker run`; pinned `v0.0.9`. Re-test newer tags before bumping.
- 2026-10-25 — Postgres init scripts — `.sql` files in `docker-entrypoint-initdb.d` don't get `POSTGRES_DB`
  as a psql variable; use `\getenv var POSTGRES_DB`. A failed init leaves a non-empty volume and init never
  re-runs: remove the `pg-data` volume (`docker volume rm venue-agents_pg-data`) after fixing the script.

- 2026-10-25 — GitHub Actions — CI failed at "Set up job" in every uv job: `astral-sh/setup-uv@v10` doesn't
  exist (setup-uv stopped publishing floating major tags after v7; releases are `v10.3.2` etc.). Pin exact
  action versions and check tags with `gh api repos/<owner>/<repo>/releases --jq '.[].tag_name'`.

- 2026-10-25 — uv prerelease — OpenTelemetry SDK 1.45 needs `semantic-conventions==0.66b1`, and the
  Azure Monitor distro a beta exporter and 0.66b0 instrumentations, so `prerelease = "explicit"` can't
  resolve. Switched to `"if-necessary"` (`if-necessary-or-explicit` is deprecated). Side effect: any
  dependency whose only match is a beta is accepted silently — review `uv.lock` diffs for new `b`/`rc`.
- 2026-10-25 — logging — a log-record factory runs before `extra=` is attached (`Logger.makeRecord`),
  so extras skipped redaction. Redact in a handler filter (`va_core.telemetry.secure_handler`); logger
  filters don't see child loggers' records.
- 2026-10-25 — pyright + pydantic-settings — a field with no default is reported as a missing
  constructor argument even though it comes from the environment, and `_env_file=` is unknown. Use
  `CoreSettings.load()`; in tests chdir to `tmp_path` instead of passing `_env_file`.
- 2026-10-25 — Python file I/O on Windows — `Path.read_text()/write_text()` default to cp1252 and CRLF:
  em-dashes became invalid UTF-8 and files flipped to CRLF. Always pass `encoding="utf-8"` (and
  `newline="\n"` when writing). Separately, escapes like `\b`, `\1`, `\n` in heredoc'd scripts from the
  Bash tool arrived as control characters — edit regex-heavy code with the Edit tool.

- 2026-10-25 — pre-commit on Windows — building the ruff-pre-commit hook env failed with
  `WinError 145 directory not empty` (antivirus file lock during pip cleanup). Run ruff as a `local`
  hook via `uv run ruff` instead; it also keeps one ruff version (uv.lock).
- 2026-10-25 — uv workspace — a glob member like `apps/*` errors on any non-empty folder without a
  `pyproject.toml` (e.g. `apps/connectors`, `apps/venue-app`); empty folders are skipped. Use `exclude`.
- 2026-10-25 — Docker Compose — `${VAR:?msg}` is checked even for services in inactive profiles, so it
  breaks plain `docker compose up`. Use `${VAR:-}` for profile-only services.
- 2026-10-25 — Agent Framework — core is GA and ships weekly (1.18→1.23 in six weeks); Learn's
  changes guide lags PyPI by several releases. Pin exactly and re-test on every bump.
- 2026-10-25 — Installing tools — `npm install -g` needs admin (Node is in Program Files); use
  `winget install pnpm.pnpm`. uv via `winget install astral-sh.uv --source winget`. New PATH needs a
  VS Code restart.
- 2026-10-25 — Norton Web Shield (HTTPS scanning) — it re-signs TLS with its own root and corrupts
  streamed responses: Microsoft Learn MCP timed out ("malformed chunked encoding"). Fix: Norton →
  Settings → Web Shield → exclusion for `learn.microsoft.com`. Tools with their own CA bundle (uv) reject
  Norton's cert: set `system-certs = true` in `%APPDATA%\uv\uv.toml`. Expect the same for other streaming
  endpoints (Foundry/APIM SSE) — add exclusions as needed.
- 2026-10-20 — Azure MCP on Windows — the first start of `@azure/mcp` via npx timed out while downloading;
  it worked on the next start. If MCP tools are missing, check Settings → Developer logs and restart.
