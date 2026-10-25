Task P0-09: action executor. Details: docs/build/phase-0-foundations.md, ADR-0005, ADR-0006.

Key points:
- apps/action-executor consumes approval events; loads the draft and approval; refuses (and logs) if
  there's no approval row or the approval doesn't match the draft's tenant.
- Handlers registered by draft type; first handler: Gmail send of an approved draft reply (sandbox only).
- Idempotency on approval_id: duplicate events never double-send; store external ids in action_log.
- This is the only app allowed write-scoped credentials. Add a test/lint check that fails if any other
  app imports executor handlers or requests write scopes.

/verify, security-reviewer (blocking), tick, /update-memory, commit.
