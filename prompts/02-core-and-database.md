Tasks P0-02 then P0-03: core package and the stamp database with row-level security.
Details: docs/build/phase-0-foundations.md, docs/architecture/data-model.md, .claude/rules/security.md,
.claude/rules/database.md, ADR-0007, ADR-0008.

Do P0-02 first, verify, commit; then P0-03.

Key points:
- TenantContext is created only from authenticated input (token or signed run envelope) and propagated
  with contextvars; nothing downstream accepts tenant ids as parameters from callers' payloads.
- Every tenant table: (tenant_id, id) primary key, tenant_id in every FK, RLS policy using
  current_setting('app.tenant_id'), created in the same migration. App DB role has no BYPASSRLS.
- The DB session helper sets app.tenant_id from TenantContext and raises if none is present.
- Use the test-writer subagent to build tests/isolation/: for every table, tenant A cannot select,
  insert, update or delete tenant B's rows, and queries fail closed with no tenant set. Make the test
  auto-discover tables so new tables without RLS fail the suite.

Before finishing: /verify, then the security-reviewer subagent; fix anything blocking.
Tick tasks, /update-memory, commit each task separately.
