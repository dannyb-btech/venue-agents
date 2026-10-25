Tasks P0-06 then P0-07: connector framework, then the first read connectors (Gmail, Drive, Tanda, Xero).
Details: docs/build/phase-0-foundations.md, docs/architecture/integrations.md, .claude/rules/connectors.md,
ADR-0005, ADR-0011.

P0-06:
- packages/core.mcp: MCP server base using the official MCP Python SDK (check current version), tenant
  middleware, tool registry that only accepts tools declared "read" or "draft" (registration fails
  otherwise), draft helper that writes a draft row and returns its id, per-tenant credential loading,
  rate limiting, structured errors.

P0-07 (one connector per commit, use /new-connector for each):
- Gmail: search_threads, get_thread (read), create_draft_reply (draft row only). Sync via history API.
- Drive: search_files, read_file (read). Changes API sync for configured folders only.
- Tanda: get_roster, get_timesheets, get_costs (read).
- Xero: list_bills, list_invoices, get_payroll_summary (read; summaries only).
- Sandbox/demo orgs only; recorded, anonymised fixtures; no live calls in CI.
- If credentials aren't available yet (P0-13), build against fixtures and say so in progress.md.

/verify, security-reviewer after each connector, update integrations.md, tick, /update-memory.
