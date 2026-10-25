# Lessons

Things that cost time: library quirks, API gotchas, environment issues. Newest first.
Format: date — area — what happened — what to do instead.

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
