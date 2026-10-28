# 0017. Control plane catalog and stamp provisioning

- **Status:** Accepted
- **Date:** 2026-10-27
- **Deciders:** Danny Byra

## Context

ADR-0008 needs a global control plane from release one: a tenant catalog (venue → stamp, region, plan,
entitlements), placement of new venues and routing for the edge
([control planes](https://learn.microsoft.com/azure/architecture/guide/multitenant/approaches/control-planes),
[Deployment Stamps](https://learn.microsoft.com/azure/architecture/patterns/deployment-stamp)).
ADR-0007 keeps the catalog in a separate global Postgres database.

Three things pull against the non-negotiables in CLAUDE.md:

- The catalog is **cross-tenant by design**: routing looks up any venue, and placement counts every
  stamp's venues. Non-negotiable 2 says every table has an RLS policy keyed on the request's tenant,
  but no request to the catalog has a tenant context.
- The stamp's `tenant` table is SELECT-only for runtime code (P0-03): creating tenants is the control
  plane's job, so the control plane needs *some* write access in every stamp, and has to act as a
  tenant it has just created, which no request authenticated as.
- Onboarding writes to two databases (catalog and stamp) with no distributed transaction.

Deployed services authenticate to Azure Database for PostgreSQL with Microsoft Entra: a managed
identity's access token is the password, the role is mapped with `pgaadauth_create_principal*`, and
tokens (5-60 minutes) are checked at login
([managed identity](https://learn.microsoft.com/azure/postgresql/security/security-connect-with-managed-identity),
[Entra users](https://learn.microsoft.com/azure/postgresql/security/security-manage-entra-users),
[configure Entra](https://learn.microsoft.com/azure/postgresql/security/security-entra-configure)).
Learn recommends `sslmode=verify-full`
([TLS](https://learn.microsoft.com/azure/postgresql/security/security-tls)).

## Decision

1. **Catalog without RLS, one role.** Catalog tables have no RLS. Only `va_catalog` members reach the
   catalog (NOLOGIN, NOBYPASSRLS, owns nothing): SELECT on `stamp`; SELECT/INSERT/UPDATE on
   `tenant_directory`, `venue_directory`, `entitlement`, `onboarding_job`; no DELETE (offboarding is a
   status; entitlements are revoked with `revoked_at`). Tenant-owned catalog rows still carry
   `tenant_id` in their keys. The catalog holds no people or business data; the only names are an
   onboarding job's inputs, kept so a failed job can be retried. Stamps are registered by operators
   with the catalog owner login (CLI), never the API. Only the control plane's settings
   (`ControlPlaneSettings`) know the catalog URL. Cross-tenant catalog queries (`/stamps`, placement)
   carry no venue ID: the stated exception to "every query carries the venue ID".
   Non-negotiable 2's RLS clause is narrowed to **stamp** tables, with this ADR as the catalog exception.
2. **A tenant lives on one stamp.** `tenant_directory (tenant_id → stamp_id)`, and `venue_directory`
   has a composite FK `(tenant_id, stamp_id)` to it (deferrable, so a later move updates both in one
   transaction): the database refuses a venue placed apart from its tenant. The phase-3 tool is
   therefore **move-tenant** (a tenant and all its venues), and a tenant larger than one stamp's
   capacity can't be onboarded (`the tenant's stamp is full`). `venue_directory.region` is the
   region the operator asked for; placement requires it to equal the stamp's region.
3. **`va_provision` in every stamp.** SELECT and INSERT on `tenant` and `venue` only. It is not an
   RLS bypass: the provisioner writes through `tenant_session`, so RLS WITH CHECK still applies.
   **The provisioning step is the only code that builds a `TenantContext` from catalog state**, for a
   tenant the control plane minted (new) or validated as `active` in `tenant_directory` (existing),
   under a `venues:write` caller, and only with `va_provision` rights. Suspend/offboard get their own
   reviewed grants when built.
4. **Placement.** An existing tenant's venues go to its stamp (it may be draining; not closed or
   full); venues can be added only to an `active` tenant, in the stamp's region. A new tenant goes to
   the operator's requested stamp if given (e.g. OR → canary; active, in region, with spare capacity),
   else the least-loaded active stamp (venues/capacity, `provisioning` venues count) of the requested
   ring (default `general`) in the region. Placements are serialised by a transaction-level advisory
   lock, so concurrent onboardings can't take a stamp's last slot (tested). Rings are `canary` and
   `general`; dedicated and AU-processing stamps (ADR-0008, ADR-0013) need a catalog migration (a
   stamp kind / dedicated tenant, excluded from automatic placement) before the first one exists.
5. **Onboarding is a job.** (1) catalog transaction: place, record tenant/venue (`provisioning`),
   entitlements, job; (2) create tenant/venue in the stamp with `INSERT … ON CONFLICT DO NOTHING`,
   retrying transient failures in-process with full-jitter backoff (3 attempts); (3) mark the job
   `succeeded` and the venue/tenant `active`. Only `active` and `suspended` venues route
   (`provisioning`, `offboarding`, `offboarded` don't). If step 2 still fails the job is `failed`
   with one of our error codes; `POST /venues/{id}/onboarding/retry` re-runs 2-3. A `running` job can
   be retried once stale (5 minutes on the database clock), and a late finisher can't overwrite a
   newer run's outcome. An `Idempotency-Key` header makes `POST /venues` safe to replay (unique on
   the job; the body is not compared). The job table is the state the durable orchestration (agent
   runtime, P0-08) will drive, so the two patterns don't fork.
6. **Callers authenticate with scoped bearer tokens** (`route:read` edge, `entitlements:read` stamp
   services, `stamps:read` / `venues:write` operators). Until P0-05 the only authenticator is a static
   dev token (32+ chars, constant-time compare), refused outside `ENVIRONMENT=local/test`, refused
   when Azure hosting variables are present, and `serve` binds loopback only with it. `CoreSettings`
   itself refuses `ENVIRONMENT=local/test` when Azure hosting variables are set (every service), so
   one wrong app setting can't switch off the deployed-only guards. Tenant IDs in this API identify
   the tenant an operator manages; see 3 for the one place they become a tenant context.
   `/docs` and `/openapi.json` are served only locally.
7. **Database logins.** Locally, password logins (`va_catalog_local`, `va_provision_local`). In Azure,
   every login is a managed identity mapped to a role that is a member of exactly one group role
   (`va_app`/`va_catalog`/`va_provision`). `create_runtime_engine` takes a password provider awaited
   for each new connection: `EntraTokenPassword` (scope
   `https://ossrdbms-aad.database.windows.net/.default`) or `SecretPassword` (local); passwords in
   the URL query are refused and bound parameters are hidden from errors. Stamp connection details
   (host, port, db, login, sslmode) are non-secret columns on the catalog `stamp` row. **Outside
   local, every catalog and stamp connection must be an Azure Postgres host
   (`*.postgres.database.azure.com`) with `sslmode=verify-full` and an explicit `sslrootcert`**
   (`system` for stamps), because the token is sent as the password. Every engine checks at first
   use that its login can't reach a superuser/BYPASSRLS role, owns no tables, and is a member of the
   expected group role and no other. **The control-plane identity is a principal on every stamp's
   Postgres server**: a cross-stamp credential, limited to `va_provision` rights.
8. **Routing and entitlements are not per-request calls.** `/route` is a lookup at sign-in or
   bootstrap whose result is carried in the token or cached at the edge; stamp services cache or
   receive entitlements. The global control plane and catalog must not become a runtime dependency
   of every venue request (ADR-0008 blast radius). The mechanism is a follow-up ADR (below).

## Options considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| RLS on tenant-owned catalog tables | Uniform rule | Routing and placement are cross-tenant, so every query needs a bypass path, which defeats it | Protection would be nominal |
| Control plane uses each stamp's admin login | Simple | A global service holding RLS-bypassing, schema-owning credentials for every stamp | Blast radius |
| One user-assigned identity per stamp, held by the control plane | Limits a leaked token to one stamp | More identities to create and rotate; the code already supports it (`password_for(stamp)`) | Not needed for two stamps; revisit below |
| Venues of one tenant on different stamps | Finer balancing | Tenant row, users and roles duplicated across stamps; cross-venue views break | Stamp-local data model (ADR-0008) |
| Synchronous two-DB write without job state | Less code | A stamp failure leaves catalog and stamp disagreeing with no way back | Not recoverable |
| Connection URLs (with passwords) in Key Vault | Familiar | No passwords exist with Entra; URLs would hold secrets needlessly | Entra tokens replace them |
| `azure-postgresql-auth` library for tokens | Official helper | Extra dependency; SQLAlchemy 2.1 compatibility and UAMI username detection unverified | Small own provider instead; revisit in P0-11 |

## Consequences

- Good: routing and onboarding work across stamps from day one (tests run on two stamp databases);
  the control plane can't read or change stamp business data; a half-finished onboarding is visible,
  unroutable and retryable; replays don't duplicate tenants; no database passwords in Azure.
- Bad / costs: the catalog relies on one role rather than RLS, so a control-plane bug can read across
  tenants' catalog rows (no business data there). One managed identity can log in to every stamp.
  The placement lock serialises onboarding globally (fine at onboarding rates). Repeated failed
  onboardings leave `provisioning` venues that count against capacity until cleaned up. The catalog
  is one regional database: its availability matters for onboarding and sign-in, not (per 8) for
  every request.
- Follow-up work:
  - **Done in ADR-0019: edge routing and entitlement distribution.** Options: stamp ID claim in
    the token with Front Door routing by header/path; per-stamp hostnames with a redirect after a
    sign-in `/route` lookup; entitlements cached by stamp services with a TTL, or pushed into a stamp
    table on change. Include catalog availability (zone-redundant HA, geo replica).
  - P0-11/P0-12 (moved from P0-05, which covered venue users only, ADR-0019): Entra service tokens
    with app roles mapped to the scopes; stamp callers get a `stamp_id` claim, and `/route` and
    entitlements filter on it.
  - P0-11: Entra admin + `pgaadauth_create_principal_with_oid` per stamp in the pipeline; verify on a
    real server that Entra-mapped roles can be members of NOLOGIN group roles and stay NOBYPASSRLS,
    that user-assigned identity OID mapping works, and that `sslrootcert=system` validates the Azure
    chain (ICA rotation 2026). Private connectivity from the control plane to every stamp's Postgres
    (private endpoints into the control-plane VNet or peering; `db_host` is the private DNS name).
  - Operator query and runbook for failed/stale onboarding jobs.
  - Phase 3: move-tenant (a `moving` status that routes read-only), offboarding grants.

## Revisit when

Onboarding volume makes the global placement lock a bottleneck; a requirement appears to split a
tenant across stamps; the first dedicated or AU-processing stamp; a security review asks for
per-stamp control-plane identities; Entra role mapping can't be made a member of group roles; or the
catalog needs to hold business or personal data.
