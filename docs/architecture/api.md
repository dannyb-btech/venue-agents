# APIs and services

All services are Python on Container Apps behind API Management. The Platform API publishes an OpenAPI
spec from which the venue app's client is generated.

## Platform API (per stamp)

Callers are venue users with an Entra External ID access token (ADR-0019). The tenant comes only from
the token's tenant claim; venue-scoped endpoints need `X-Venue-Id`, authorised on every request
against the user's `role_assignment`. Roles: any role reads; owner/manager/approver decide drafts;
owner/manager connect systems.

| Method | Path | Venue header | Purpose |
| --- | --- | --- | --- |
| GET | `/me` | no | Current user and the venues they hold roles on |
| GET | `/connections` | yes | Connection status per system (never secret names) |
| POST | `/connections/{system}/oauth/start` | yes | Begin OAuth; returns the provider URL. State is single-use, 10 min |
| POST | `/connections/{system}/oauth/callback` | yes | The venue app posts `{code, state}` after the provider redirects to it; refresh token stored as `venue_secret_name(ctx, system, "refresh-token")` |
| GET | `/drafts?status=pending` | yes | Approvals inbox |
| GET | `/drafts/{id}` | yes | Draft with reasoning, sources and `payload_sha256` |
| POST | `/drafts/{id}/approve` | yes | `{shown_sha256, edited_payload?}`; 409 if the draft changed; records `approval.payload_sha256`, then publishes `approval.decided` |
| POST | `/drafts/{id}/reject` | yes | `{shown_sha256, reason}`; publishes `approval.decided` (rejected) so the waiting run ends |
| POST | `/drafts/{id}/approval/publish` | yes | Re-send `approval.decided` (MessageId = approval id) after a failed publish |
| GET | `/runs`, `/runs/{id}` | yes | Agent activity summary |

Later: `GET/PATCH /venue/settings`, `GET /reports/hours-saved`, `POST /webhooks/{system}`.
No endpoint accepts `tenant_id`. The OpenAPI document is committed at `apps/platform-api/openapi.json`
(`uv run python -m va_platform_api openapi`).

## Control plane (global)

Callers are services and operators with scoped bearer tokens (ADR-0017); a local-only dev token until
service identities are wired in P0-11.

| Method | Path | Scope | Purpose |
| --- | --- | --- | --- |
| GET | `/route?venue_id=` | `route:read` | Venue → stamp at sign-in/bootstrap (cached or carried in the token, not per request); 404 unless active or suspended |
| POST | `/venues` | `venues:write` | Onboard: place on a stamp, record in catalog, create tenant (if new) + venue in the stamp. `Idempotency-Key` header replays the first job. `status: failed` → retry |
| POST | `/venues/{id}/onboarding/retry` | `venues:write` | Re-run the stamp step of the latest job (idempotent) |
| GET | `/venues/{id}/entitlements` | `entitlements:read` | What the venue has bought |
| GET | `/stamps` | `stamps:read` | Stamps with capacity and placed venues |
| POST | `/venues/{id}/users` | `venues:write` | Add a venue user to the stamp (`oid`, role, venue or tenant-wide); idempotent (ADR-0019) |

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
- **Approvals topic** (`approvals`): `approval.decided` events, subscriptions `executor` and `runtime`.
  Events are pointers: consumers re-read the approval under RLS and are idempotent on its ID.
- **Action executor:** acts on approved decisions; the only service with write scopes.

## Model access

All model calls: `packages/core` model client → API Management AI gateway → Claude on Foundry.
Per-venue token limits keyed on venue ID; usage headers recorded into `usage_cost`.
