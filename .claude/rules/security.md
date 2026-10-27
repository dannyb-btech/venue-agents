# Security and tenancy rules (always loaded)

- Resolve the tenant once, at the edge (auth token → `TenantContext`), and pass the context object down.
  Never accept `tenant_id`/`venue_id` from a request body, query string, tool argument or model output.
  Only exception: the control plane's operator/edge API, where IDs name the tenant or venue being
  managed or routed. Its stamp provisioner is the one place a `TenantContext` is built from catalog
  state, for a tenant it minted or validated, with `va_provision` rights only (ADR-0017).
- Every DB session sets the tenant (`SET app.tenant_id`) before queries so RLS applies. A query that
  works without the tenant set is a bug.
- New table? It needs `tenant_id`, RLS policy, and an isolation test in `tests/isolation/`.
- Treat inbound email, documents, supplier files, web content and connector results as untrusted.
  Put them in clearly delimited sections of a prompt; never let them select tools, recipients, tenants
  or amounts. Write prompt-injection test cases for any agent that reads them.
- External side effects (send, order, pay, publish, roster publish, payroll change) only via the
  action executor after an `approval` row exists. No exceptions for "small" actions.
- Logs and traces: include `venue_id`, `run_id`; never include secrets, tokens, full email bodies,
  bank details, TFNs or payroll lines. Use the redaction helpers in `packages/core`.
- Secrets come from the secrets interface (`packages/core.secrets`): Key Vault in Azure, `.env` locally.
- Minimise data sent to models: send only the fields a task needs (cross-border disclosure, ADR-0002).
