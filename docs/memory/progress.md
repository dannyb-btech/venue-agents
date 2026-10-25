# Progress

## Current state

- **Phase:** 0 — Foundations (in progress)
- **Last task done:** P0-01 Monorepo bootstrap (commit on `dev`; not pushed)
- **Next task:** P0-02 Core package (in parallel: P0-13 access requests, human)
- **Blockers:** Toast access route for OR unknown; SevenRooms credentials not yet requested;
  no Azure subscription for this project yet (local development only until then)
- **Human to do:** create the GitHub repo, push `main` and `dev`, confirm CI is green;
  start Docker Desktop and run `docker compose up -d` once to confirm the local services start
- **Environment:** Windows, VS Code + Claude Code, uv 0.13.5 (Python 3.12.15), pnpm, Docker Desktop.
  Norton Web Shield intercepts HTTPS: see lessons.md
- **Branches:** `main` (scaffold) and `dev` (work); humans merge to `main` and push

## Session log (newest first)

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
