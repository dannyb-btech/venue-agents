---
description: Scaffold a new role agent (Agent Framework agent, durable orchestration, tool allow-list, approval rules, eval set). Use when adding an agent such as Events Manager, Wine Buyer, Office Manager, Admin Assistant or Marketing.
disable-model-invocation: true
argument-hint: <agent-name>
---

# New agent: $0

1. Read `docs/architecture/agents.md` (the agent's triggers, reads, drafts, approvals, model tier)
   and `.claude/rules/agents.md`. Check current Agent Framework + durable extension APIs with
   `azure-researcher` before writing code.
2. Create `apps/agent-runtime/src/agent_runtime/agents/$0/`:
   - `instructions.md` — role, venue context placeholders, tone, hard rules (never promise prices or
     dates not in source data; flag uncertainty), output format. Version header at the top.
   - `tools.py` — explicit allow-list of connector tools (read + draft only).
   - `orchestration.py` — durable orchestration: gather context (activities) → call agent →
     create drafts → wait for approval event (with timeout + reminder) → hand to executor.
   - `config.py` — model tier, schedule (staggered per venue), approval rules, cost budget per run.
3. Triggers: register schedule/webhook/Service Bus triggers; stagger scheduled runs across a window.
4. Evals: `evals/$0/` with at least 20 anonymised real cases, 5 prompt-injection cases, a grader,
   and `scores.md` recording the baseline.
5. Venue app: make sure the draft types render clearly in the approvals inbox.
6. Run `/verify`, then `security-reviewer`. Update `docs/architecture/agents.md` with anything that changed.
