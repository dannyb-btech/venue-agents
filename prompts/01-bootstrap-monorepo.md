Task P0-01: bootstrap the monorepo. Details and acceptance criteria: docs/build/phase-0-foundations.md.

Context: Windows dev machine with Docker Desktop, Git for Windows, Python 3.12, uv, Node LTS, pnpm.
Commands must work in PowerShell and Git Bash.

Plan first (plan mode), then:
- Create the uv workspace: root pyproject.toml with members apps/* (Python apps only), apps/connectors/*,
  packages/core, packages/db. Each member has src/ layout and its own pyproject.
- Configure ruff (format + lint), pyright (strict for packages/, basic for apps/), pytest + pytest-asyncio,
  pre-commit hooks (ruff, secret scanning).
- docker-compose.yml: Postgres 16 with pgvector, Azurite, Service Bus emulator, Durable Task Scheduler
  emulator. Use the azure-researcher subagent to get current emulator images and settings.
- GitHub Actions CI: lint, types, tests, isolation tests, secret scan, on PRs and main.
- Placeholder test in each member so pytest collects something.
- Don't scaffold the Next.js app yet (P0-10).

Done when the acceptance criteria pass on a fresh clone. Update the Commands section of CLAUDE.md if
anything differs, tick P0-01, run /update-memory, commit "P0-01: bootstrap monorepo".
