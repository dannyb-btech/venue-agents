Task P0-08: agent runtime skeleton. Details: docs/build/phase-0-foundations.md, docs/architecture/agents.md,
.claude/rules/agents.md, ADR-0003, ADR-0004.

Before writing code, use the azure-researcher subagent to get, from Microsoft Learn:
- current Microsoft Agent Framework Python packages, its Anthropic client for Claude on Foundry, and the
  durable extension (exact package names, versions, minimal samples);
- how to run against the Durable Task Scheduler emulator locally;
- how to wire MCP tools into an Agent Framework agent.
Record versions and any surprises in docs/memory/lessons.md, and pin exact versions.

Build:
- Model client in packages/core: provider-agnostic interface (ADR-0013), gateway URL from settings,
  model tiers from config, usage capture into usage_cost.
- A `hello` agent: reads one connector tool, creates one draft, waits for an approval external event
  (with timeout), then records the outcome. Orchestrator code deterministic; I/O in activities.
- Run envelope: tenant context travels in a signed envelope on the Service Bus message; the runtime
  rejects unsigned or mismatched envelopes.
- Tests with a mocked model client; one manual run against a Foundry dev deployment if credentials exist.
- Prove a run survives a worker restart while waiting for approval.

/verify, security-reviewer, architect-reviewer, tick, /update-memory, commit.
