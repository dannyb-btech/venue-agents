---
description: Run every project check (lint, format, types, tests, tenant-isolation tests, evals if agents changed, Bicep build if infra changed) and report results. Use before marking any task done or committing.
argument-hint: "[quick]"
---

# Verify

Run in order and stop to fix failures (with `quick`, skip evals and the venue app):

1. `uv run ruff check . && uv run ruff format --check .`
2. `uv run pyright`
3. `uv run pytest -q`
4. `uv run pytest tests/isolation -q` — must pass; a failure here blocks everything.
5. If `apps/agent-runtime/**` or `evals/**` changed: run the evals for affected agents
   (`uv run python -m evals.run <agent>`) and compare with the last recorded scores in `evals/<agent>/scores.md`.
6. If `apps/venue-app/**` changed: `pnpm --dir apps/venue-app lint && pnpm --dir apps/venue-app test`.
7. If `infra/**` changed: `az bicep build --file infra/bicep/main.bicep` and `az bicep lint`.
8. Scan the diff for secrets, tenant IDs taken from inputs, and any external mutating call outside
   `apps/action-executor` (`git diff --stat` then read changed files).

Before P0-01 exists some commands won't; say which checks were skipped and why.
Report a short table: check, result, notes.
