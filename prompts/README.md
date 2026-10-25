# Session prompts

Paste one prompt per Claude Code session (start a fresh session for each). Each prompt points Claude at
the right task, docs and checks; the task details live in `docs/build/`. `/build-phase <n> <task>` does
the same thing generically; these prompts add task-specific guidance.

| Prompt | Task(s) |
| --- | --- |
| [00-orientation](00-orientation.md) | Read-only walkthrough; no code |
| [01-bootstrap-monorepo](01-bootstrap-monorepo.md) | P0-01 |
| [02-core-and-database](02-core-and-database.md) | P0-02, P0-03 |
| [03-control-plane](03-control-plane.md) | P0-04, P0-12 |
| [04-platform-api](04-platform-api.md) | P0-05 |
| [05-connector-framework](05-connector-framework.md) | P0-06, P0-07 |
| [06-agent-runtime](06-agent-runtime.md) | P0-08 |
| [07-action-executor](07-action-executor.md) | P0-09 |
| [08-approvals-inbox](08-approvals-inbox.md) | P0-10 |
| [09-infrastructure](09-infrastructure.md) | P0-11 |
| [10-phase-1-events-manager](10-phase-1-events-manager.md) | Phase 1 |
| [11-phase-2-wine-office](11-phase-2-wine-office.md) | Phase 2 |
| [12-phase-3-productise](12-phase-3-productise.md) | Phase 3 |
| [99-session-wrap-up](99-session-wrap-up.md) | End of any session |

Tips: use plan mode for each task; interrupt early if the plan drifts; keep one task per session when
tasks are large; run `/verify` and `/update-memory` before ending.
