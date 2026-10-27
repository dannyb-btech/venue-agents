# APIs and services

All services are Python on Container Apps behind API Management. The Platform API publishes an OpenAPI
spec from which the venue app's client is generated.

## Platform API (per stamp)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/me` | Current user, venue, roles |
| GET/PATCH | `/venue/settings` | Targets, approval rules, notification prefs |
| GET | `/connections` | Connection status per system |
| POST | `/connections/{system}/oauth/start` | Begin OAuth; returns redirect URL |
| GET | `/connections/{system}/oauth/callback` | Complete OAuth; store secret in Key Vault |
| GET | `/drafts?status=pending` | Approvals inbox |
| GET | `/drafts/{id}` | Draft with reasoning summary and sources |
| POST | `/drafts/{id}/approve` | Approve (optional edited payload) → raises approval event |
| POST | `/drafts/{id}/reject` | Reject with reason |
| GET | `/runs`, `/runs/{id}` | Agent activity and traces summary |
| GET | `/reports/hours-saved` | Weekly value report |
| POST | `/webhooks/{system}` | Inbound webhooks (signature-verified) → Service Bus |

Tenant always comes from the token. No endpoint accepts tenant_id.

## Control plane (global)

Callers are services and operators with scoped bearer tokens (ADR-0017); Entra validation from P0-05,
a local-only dev token before then.

| Method | Path | Scope | Purpose |
| --- | --- | --- | --- |
| GET | `/route?venue_id=` | `route:read` | Venue → stamp at sign-in/bootstrap (cached or carried in the token, not per request); 404 unless active or suspended |
| POST | `/venues` | `venues:write` | Onboard: place on a stamp, record in catalog, create tenant (if new) + venue in the stamp. `Idempotency-Key` header replays the first job. `status: failed` → retry |
| POST | `/venues/{id}/onboarding/retry` | `venues:write` | Re-run the stamp step of the latest job (idempotent) |
| GET | `/venues/{id}/entitlements` | `entitlements:read` | What the venue has bought |
| GET | `/stamps` | `stamps:read` | Stamps with capacity and placed venues |

Later: `POST /venues/{id}/move`, `POST /venues/{id}/offboard`. Stamps are registered with the CLI
(`python -m va_control_plane register-stamp`), not the API. `tenant_id` in `POST /venues` names the
tenant an operator is managing; it is never a tenant context for venue data.

## Connector MCP servers (per stamp, internal)

Built on `va_core.mcp` (ADR-0018). Every MCP request carries a signed run envelope
(`X-VA-Run-Envelope`: HS256 per-stamp key, tenant/venue/stamp/run/agent and the agent's tool allow-list
for that connector, ≤ 10 min); tools never take a tenant argument. Stateless streamable HTTP at `/mcp`,
`GET /healthz`. Tool naming `<system>_<verb>_<noun>` (Claude tool names allow no dots); draft tools
`<system>_draft_<noun>`. Examples:
`gmail_search_threads`, `gmail_get_thread`, `gmail_draft_reply` (draft row only),
`drive_search_files`, `drive_read_file`, `sevenrooms_get_guest`, `sevenrooms_list_reservations`,
`toast_get_sales`, `toast_get_stock`, `tanda_get_roster`, `tanda_get_timesheets`,
`tanda_draft_roster`, `xero_list_bills`, `xero_draft_bill`, `stripe_draft_payment_link`.

## Internal services

- **Agent runtime:** started by Service Bus messages and schedules; no public endpoint.
- **Sync workers:** Container Apps jobs (scheduled and event-driven).
- **Action executor:** consumes `approval.granted` events; the only service with write scopes.

## Model access

All model calls: `packages/core` model client → API Management AI gateway → Claude on Foundry.
Per-venue token limits keyed on venue ID; usage headers recorded into `usage_cost`.
