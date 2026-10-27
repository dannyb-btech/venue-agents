# 0018. Signed run envelopes and the connector framework

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Danny Byra

## Context

ADR-0005 makes each external system an MCP server exposing read and draft tools only. The overview
places these servers as internal Container Apps in each stamp, separate from the agent runtime, so
every tool call crosses a network hop. Non-negotiable 2 says the tenant must come from the
authenticated context, never from tool arguments or model output.

This ADR defines the **runtime → connector** envelope. The overview also mentions an envelope on the
Service Bus trigger message (trigger → orchestration); trusting that message is P0-08's decision and
is not covered here.

Facts and constraints:

- **Foundry's MCP guidance:** allow-list tools, treat tool results as untrusted, require approval
  for writes ([MCP best practices](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol#best-practices)).
- **The MCP Python SDK is at 2.x** (`mcp==2.3.0`, 2026-10-02):
  - FastMCP was renamed `MCPServer`, and it has context-tier `ServerMiddleware` that sees the method,
    raw params and the HTTP request ([migration guide](https://py.sdk.modelcontextprotocol.io/v2/migration/)).
  - Agent Framework core 1.23 requires `mcp>=2.2,<3`, so the connector base and the agent runtime can
    share one lockfile.
- **Stateful streamable HTTP keeps a session task alive** past the HTTP request that created it.
  Stateless mode (`stateless_http=True`) runs each request on its own.
- **The SDK silently drops unknown tool arguments.** Its result caching (SEP-2549) would also let a
  client reuse a `tools/list` result unless it is marked private and immediately stale.
- **Claude tool names must match `^[a-zA-Z0-9_-]{1,64}$`.** The dotted names in our docs
  (`gmail.search_threads`) are not valid tool names.
- **A tenant can have several venues**, and `connection` is unique per `(tenant, venue, system)`, so
  each venue can connect its own Gmail mailbox or Xero org.
- **No Azure subscription exists yet.** Service-to-service auth on Container Apps is P0-11 work, so
  the tenant proof has to work locally first.

## Decision

1. **Run envelope.** The agent runtime mints a short-lived JWT (HS256) for one connector:
   - **Claims:** `iss=va-agent-runtime`, `aud=va-connector:<system>`, `sub=<agent>`, `tid`, `vid`,
     `stamp`, `run`, `tools` (that agent's allow-list for the system), `iat`, `exp` (≤ 600 s), `jti`.
   - **Transport:** the `X-VA-Run-Envelope` header on every MCP request.
   - **When it is minted:** per MCP HTTP request, through an issuer hook on the runtime's MCP HTTP
     client. If Agent Framework only allows fixed headers, per activity is the fallback, with a TTL
     that covers the activity.
   - **Run row:** the `agent_run` row must exist before an envelope is minted.
   - **Signing:** a **per-stamp key** from the secrets interface: `run-envelope-key-<kid>`, 32 random
     bytes, base64- or hex-encoded (43+ characters).
   - **Rotation:** `RUN_ENVELOPE_KIDS` lists the keys. The first one signs and all of them verify, so
     a key rotates by adding the new kid first and removing the old one later.
   - **What connectors verify:**
     - the algorithm is pinned to HS256;
     - the kid is known;
     - issuer and audience;
     - the stamp equals the connector's own `STAMP_ID`;
     - expiry, with 30 s leeway;
     - lifetime ≤ 600 s;
     - all claims are present;
     - every listed tool carries the connector's prefix.
   - **Tenant source:** the `TenantContext` comes from the envelope only.
   - **Replay:** an envelope can be replayed within its lifetime. `jti` is for trace correlation, not
     replay detection. Headers must never be logged (P0-08/P0-11 check).
   - **Swappable:** verification sits behind an `EnvelopeVerifier` interface.
2. **Connector framework** (`va_core.mcp`, installed with `va-core[mcp]`):
   - **Registry:** `ToolRegistry` refuses, at start-up:
     - any category other than read/draft (so a "write" tool can't register);
     - names other than `<system>_<verb>_<noun>`;
     - duplicate names;
     - argument models without `extra="forbid"`;
     - argument models with aliases;
     - argument fields named `tenant_id`/`venue_id`/`stamp_id`/`run_id`.
   - **Middleware:** it verifies the envelope on every request and serves only `initialize`, `ping`,
     `server/discover`, `tools/list` and `tools/call`. Resources, prompts, completions and extension
     methods are refused. Then:
     - `tools/list` is filtered to the allow-list and marked private and immediately stale;
     - `tools/call` is refused unless the tool is registered *and* allow-listed;
     - raw arguments are validated against the model, so unknown fields are refused, not dropped;
     - then rate limits apply, and the call runs inside `tenant_scope` and `run_scope` with a span.
   - **Errors:** a caller (and so the model) gets error codes, plus `retry_after` on rate limits.
     It never gets exception text, argument values, unknown field names or secret names. Details go
     to the redacted logs.
   - **ToolContext:** handlers get a per-call `ToolContext` in which everything is bound to the
     envelope's tenant, venue and run:
     - `TenantSecrets`, never the raw store;
     - `TenantCredentials`, with a connection lookup for this venue;
     - a `create_draft` closure, present for draft tools only, that writes only that system's draft
       types.
   - **Transport:** stateless HTTP with server-sent-event responses. DNS-rebinding protection is on;
     allowed hosts are a setting, with wildcards refused.
     - **mcp 2.3.0 transport bug:** stateless JSON responses arrive empty under uvicorn, and the
       2026-07-28 protocol path (`server/discover`) fails there. The handshake-era protocol works, and
       the SDK's ASGI test transport hides both.
     - **Coverage:** a test now makes one call over a real uvicorn socket.
   - **Postgres side:** `va_db.connectors` holds `SqlDraftWriter` (the run must be this venue's and
     still open), `SqlConnectionStore` and `PgRefreshLock`. `va_db.connector_deps.build_connector_deps`
     is the one start-up path, so every connector verifies the same way.
3. **Credentials are per venue connection** (`va_core.credentials`, no `mcp` dependency, so sync
   workers and the executor use it too):
   - **Secret names:** a connection's `secret_ref` names its secret,
     `t-<tenant>-<venue>-<system>-<kind>` (`venue_secret_name`).
   - **Cache key:** access tokens are cached per credential, (tenant, secret ref), and every call
     re-reads the connection, so revoking it takes effect at once.
   - **Refresh path:** refreshing is serialised per credential with an in-process lock, then a
     Postgres advisory lock (`pg_try_advisory_xact_lock`, bounded wait), then a refresh timeout.
     Inside the lock the framework reads the refresh token uncached (its name must end in `-token`),
     calls the vendor-specific refresh, and stores a rotated token before releasing.
   - **One path for everyone:** every refresher (connectors, sync workers, OAuth callback) uses this
     path.
4. **Tool names** are `<system>_<verb>_<noun>` everywhere: allow-lists, logs, `tool_call.tool`. Draft
   tools are `<system>_draft_<noun>` (`gmail_draft_reply`, `xero_draft_bill`), so their category is
   visible. Draft *types* stay dotted (`gmail.reply`): they are not tool names.
5. **Shared types outside the MCP namespace:**
   - `ToolCategory` lives in `va_core.tools` and the credential types in `va_core.credentials`.
   - `va_db` imports neither `mcp` nor `jwt`; a test checks this.
   - mcp 2.x is pinned exactly and re-tested on every bump.

## Options considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| HS256 per-stamp key (chosen) | Simple and fast; works locally with no Azure dependency; rotation by kid | Any holder of the key (runtime and every connector) can mint envelopes | Accepted: a connector already holds a DB login that can set any tenant. Separation comes from per-service roles (H1) and network isolation |
| ES256: runtime signs, connectors only verify | A compromised connector can't mint envelopes | Needs Key Vault key or private-key plumbing now, plus more moving parts before there's a deployment | Overkill until per-service DB roles exist; the verifier interface leaves the door open |
| Entra service tokens + unsigned tenant header | No crypto of our own | The tenant claim isn't signed; can't run locally without Azure | Fails non-negotiable 2's intent locally. Needed later as a *second* layer anyway |
| Tenant in tool arguments or MCP `_meta` | No extra mechanism | The model, or injected content, could choose the tenant | Violates non-negotiable 2 |
| Dotted names with a wire alias (`gmail__search_threads`) | Keeps existing docs | Two names to keep in sync; mismatches in logs and allow-lists | More error-prone than one name |
| Stateful streamable HTTP | Fewer round trips per run | Session tasks outlive requests, so context could leak across tenants | Isolation risk |
| Credentials keyed per tenant + system | Simpler names | One venue's token served to another venue of the same tenant | Cross-venue leak (found in review) |

## Consequences

- Good:
  - The tenant can't be set by a model, tool argument or content.
  - Allow-lists are enforced inside the connector.
  - A "write" tool can't register.
  - Each venue's credentials stay separate.
  - One framework and one start-up path for all P0-07 connectors.
  - Isolation tests cover drafts (tenant, venue, run), connections, cross-tenant secret refs,
    two venues in one tenant, and refresh locks.
- Bad / costs:
  - The HS256 key is shared by the runtime and every connector in a stamp.
  - Rate limits and the access-token cache are per replica, so each replica refreshes once per
    expiry.
  - Every tool call reads the connection row once.
  - A refresh holds a pooled DB connection for the length of the vendor call; refresh functions must
    not use the DB.
  - Connectors need Key Vault *set* rights to store rotated refresh tokens. With vault-scoped RBAC,
    that is write access to every tenant's secrets in the stamp. It isn't an external write scope,
    but it is a privilege to scope in P0-11.
  - The server tests depend on mcp 2.x internals (middleware sees `tools/list` results as dicts).
- Follow-up work:
  - **P0-05:** the OAuth callback stores tokens as `venue_secret_name(ctx, system, "refresh-token")`
    and refreshes through `TenantCredentials`.
  - **P0-08:**
    - the runtime mints envelopes per request (confirm Agent Framework's MCP tool supports a header
      hook, via `azure-researcher`);
    - write `tool_call` rows;
    - add a `va_connector` DB role (SELECT `connection`, `agent_run`; INSERT `draft`) to the H1 role
      set;
    - add a draft idempotency key (`(tenant_id, run_id, idempotency_key)` unique) so retried
      activities don't create duplicate drafts;
    - decide how the trigger message is trusted;
    - make sure Agent Framework's MCP client works against the stateless server: handshake-era
      protocol, or an mcp release that fixes the 2026-07-28 path under uvicorn.
  - **P0-09:** executor write grants use a separate connection secret kind and lock key from read
    grants.
  - **P0-11:**
    - Container Apps internal ingress plus managed-identity auth between runtime and connectors, as a
      second layer;
    - envelope keys in Key Vault per stamp;
    - scoped Key Vault set rights;
    - allowed hosts set to the internal FQDN;
    - check APIM, Agent Framework and OTel instrumentation don't record request headers.
  - Later: cross-replica rate limits (Postgres or Redis) if a vendor's per-app limit needs it.

## Revisit when

- A connector or the runtime runs outside the stamp's trust boundary.
- Per-service DB roles make connectors otherwise unable to act for another tenant (then move to ES256
  so connectors can't mint).
- A vendor's per-app limit is hit across replicas.
- Agent Framework can't send per-run headers.
- mcp 3.x ships, or a 2.x release fixes stateless JSON responses or the 2026-07-28 path.
