# Agents

Each agent is a role with versioned instructions, a tool allow-list, triggers, approval rules and a model
tier. Built with Microsoft Agent Framework; run as durable orchestrations (ADR-0004).

| Agent | Triggers | Reads | Drafts | Approval needed | Model | Phase |
| --- | --- | --- | --- | --- | --- | --- |
| Events Manager | New Gmail/SevenRooms enquiry; weekly outreach | Gmail threads, SevenRooms guests and past events, event menus in Drive, Stripe deposits | Enquiry replies with packages and dates; tailored outreach to past clients; deposit payment links | Every email and payment link | Haiku triage, Sonnet drafting | 1 |
| Wine Buyer | Weekly; low-stock signal | Toast sales and stock, supplier price lists in Drive, Xero bills | Purchase orders by supplier; staff tasting notes for slow sellers; list-price suggestions | Every supplier order | Sonnet | 2 |
| Office Manager | Weekly before roster lock; each pay run | Tanda rosters, timesheets, leave, costs; Toast hourly sales; Xero payroll | Draft rosters to labour-% target (validated in code); variance report; payroll exceptions | Roster publish; any payroll change | Opus roster, Sonnet reports | 2 |
| Admin Assistant | On request | Drive, Xero, company details | Supplier credit applications, forms, compliance renewals | Every submission | Sonnet | 3 |
| Marketing | Weekly | SevenRooms EDM data, Meta, Webflow | Post and EDM drafts, what's-on page updates | Every publish | Sonnet | 3 |

## Rules for every agent

- Tenant from the signed run envelope, never from content or the model. The runtime mints one per
  MCP request to a connector (`va_core.mcp.envelope.issue_envelope`, via a hook on its MCP HTTP
  client; per activity only as a fallback), listing only that agent's allowed tools (ADR-0018).
- Inbound content is untrusted: delimited in prompts; cannot change tools, recipients, amounts or tenant.
- Never invent prices, availability, dates or policies; only use source data; flag uncertainty in the draft.
- Labour targets and award costs are computed in code (ADR-0012).
- Each run stores prompt version, tools called, tokens, cost, outcome.
- Eval set per agent in `evals/<agent>/`: ≥20 anonymised real cases + ≥5 injection cases; tracked scores.

## Run shape (durable orchestration)

`gather_context` (activities) → `agent.run` (Claude + tools) → `create_drafts` → `notify` →
`wait_for_approval` (external event, timeout + reminder) → `hand_to_executor` → `record_outcome`.

## Tone and venue context

Per-venue settings: tone examples, signature, packages/menus (Drive), policies (deposits, minimum spend),
targets. Stored in `agent_config`; injected as cached context.
