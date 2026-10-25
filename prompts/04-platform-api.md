Task P0-05: Platform API skeleton. Details: docs/build/phase-0-foundations.md, docs/architecture/api.md.

Key points:
- FastAPI app in apps/platform-api. Auth: validate Entra External ID tokens (use azure-researcher for
  current token validation guidance); a dev-mode stub issuer for local work, disabled outside local.
- Roles owner, manager, approver, viewer enforced server-side; viewers cannot approve.
- No endpoint accepts tenant_id or venue_id; the token determines them.
- Approve/reject write an approval row and publish an approval event to Service Bus (emulator locally)
  with the approval id as message id for idempotency.
- OAuth start/callback scaffolding for connections stores only a Key Vault secret reference in the DB.
- Publish OpenAPI; add a test that fails if any request schema contains tenant_id or venue_id.

/verify, security-reviewer, tick, /update-memory, commit.
