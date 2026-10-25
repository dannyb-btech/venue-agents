---
paths:
  - "apps/agent-runtime/**"
  - "evals/**"
---

# Agent runtime rules

- Agents are built with Microsoft Agent Framework (Python) using the Anthropic-on-Foundry client,
  pointed at the AI gateway URL, never directly at Foundry in deployed code. (ADR-0004)
- Each agent lives in `apps/agent-runtime/src/agent_runtime/agents/<agent>/` with:
  `instructions.md` (versioned prompt), `tools.py` (explicit allow-list), `orchestration.py`
  (durable orchestration), `config.py` (model tier, schedule, approval rules).
- Orchestrations are deterministic: no clock, random or I/O in orchestrator code; do that in activities.
  Gather context with deterministic activities (SQL, connector reads) before calling the model.
- Write tools create drafts only. The orchestration then waits for an approval external event.
- Model tiers: Haiku for triage/classification, Sonnet for drafting, Opus only where configured
  (roster optimisation). Use prompt caching for stable instructions and venue context.
- Every run records: prompt version, model, tools called, tokens, cost, outcome, venue_id.
- Every agent has an eval set in `evals/<agent>/` (anonymised real cases + injection cases).
  Prompt changes must not regress evals; run them before marking a task done.
- Agent Framework is prerelease: pin versions; check APIs with the `azure-researcher` subagent.
