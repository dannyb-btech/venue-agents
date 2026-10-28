# 0019. Venue user identity, tenant resolution and single-stamp routing

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Danny Byra

## Context

The Platform API (P0-05) serves venue staff signed in to the venue app. Every stamp table is under
forced RLS (ADR-0007), so the API must know the user's **tenant** before it can read anything,
including the user's own `app_user` and `role_assignment` rows. Non-negotiable 2 says the tenant comes
from the authenticated context, never from a request. ADR-0017 left two follow-ups for this point:
how requests reach the right stamp, and how stamp services get entitlements.

Microsoft's multitenant guidance:

- **Use a managed IdP; building your own is an antipattern.** For customers without Entra ID, use
  External ID
  ([identity approaches](https://learn.microsoft.com/azure/architecture/guide/multitenant/approaches/identity)).
- **Two ways to map users to tenants.** Either embed a tenant identifier claim in the token, or keep
  the IdP tenant-agnostic and hold tenant access records in the application tier. The association is
  keyed on immutable IDs (Entra `oid`), never email
  ([identity considerations](https://learn.microsoft.com/azure/architecture/guide/multitenant/considerations/identity)).
- **Claims refresh only when a token is reissued**, which limits claim-based authorisation
  ([WAF SaaS identity](https://learn.microsoft.com/azure/well-architected/saas/identity-access)).
- **Tenant identifiers in URLs, headers or query strings are untrusted routing hints.** Prefer a
  tenant claim from a validated token, then authorise the caller for that tenant
  ([map requests to tenants](https://learn.microsoft.com/azure/architecture/guide/multitenant/considerations/map-requests),
  [APIM multitenancy](https://learn.microsoft.com/azure/architecture/guide/multitenant/service/api-management)).
- **Stamp routing goes through a central service** that resolves tenant → stamp: APIM
  `validate-jwt` plus `set-backend-service`, or a cached tenant map. Front Door rules can't read JWT
  claims
  ([Deployment Stamps](https://learn.microsoft.com/azure/architecture/patterns/deployment-stamp)).

What External ID tokens look like:

- **Issuer and audience:** issuer is `https://{tenantId}.ciamlogin.com/{tenantId}/v2.0`, read from
  the metadata document. `aud` is the API's client ID for v2 tokens, which needs
  `accessTokenAcceptedVersion: 2`. `scp` carries delegated scopes. `oid` is the stable user ID
  ([claims validation](https://learn.microsoft.com/entra/identity-platform/claims-validation),
  [access token claims](https://learn.microsoft.com/entra/identity-platform/access-token-claims-reference)).
- **Custom user attributes** can be emitted as claims through the app's Attributes & Claims
  ([add attributes to token](https://learn.microsoft.com/entra/external-id/customers/how-to-add-attributes-to-token)).
  Learn doesn't confirm they reach *access tokens* for a custom API.
- **Custom claims provider:** an `OnTokenIssuanceStart` custom claims provider is the documented
  alternative. It calls our REST API at issuance, has a 2-second timeout and must be public
  ([custom extensions](https://learn.microsoft.com/entra/external-id/customers/concept-custom-extensions)).

Constraints: one pilot venue (OR), one stamp (canary), and no Azure subscription yet, so everything
must also run locally.

## Decision

1. **Authentication: Entra External ID access tokens**, validated by the Platform API:
   - RS256 only, keys from the metadata's `jwks_uri`;
   - issuer equals the metadata issuer, `aud` equals the API client ID, `tid` equals our External
     ID tenant;
   - `scp` contains the API scope, and `oid` is present;
   - users are keyed on `oid` (`app_user.external_subject`).
2. **Tenant: one claim set by us.** Each user carries a custom attribute holding our tenant ID,
   emitted as a claim whose name is a setting. Operators set it when inviting the user; Graph
   automation comes later. If the attribute can't reach access tokens, the fallback is the
   `OnTokenIssuanceStart` claims provider emitting the same claim; the API doesn't change. The
   tenant comes only from this validated claim.
3. **Authorisation: in our database, per request.**
   - With the tenant set, the API loads the active `app_user` for `oid` and its `role_assignment`
     rows. A NULL venue in `role_assignment` means every venue of the tenant.
   - The IdP holds no roles, so role changes apply on the next request.
   - A user belongs to one tenant for now.
4. **Venue: a selector, always authorised.**
   - Venue-scoped endpoints take the venue in the `X-Venue-Id` header.
   - Every request checks it against the user's role assignments and an active `venue` row of the
     token's tenant. RLS makes another tenant's venue invisible anyway.
   - `venue_id` may now come from a request in this one form, an authorised selector.
     `tenant_id` never may.
5. **Stamp check.** The token's tenant must exist (`active`) in this stamp's database; otherwise
   403 `tenant_not_on_stamp`. This closes P0-02 follow-up L2.
6. **One stamp until there are two.**
   - Nothing routes today.
   - When the second stamp is built: APIM validates the token, maps tenant → stamp from a cache
     filled from the control plane (`/route`, keyed by tenant), and sets the backend. Moves
     invalidate the cache.
   - The control plane stays off the per-request path (ADR-0017 §8).
7. **Entitlements.** Stamp services read `/venues/{id}/entitlements` from the control plane, cached
   with a TTL. Built in P0-12.
8. **Users are created in stamps by the control plane.** Operators call `POST /venues/{id}/users`.
   The provisioner (ADR-0017 §3) inserts `app_user` and `role_assignment`, so `va_provision`
   gains SELECT/INSERT on those two tables.
9. **Local development:** a dev token issuer (HS256, same claims), refused outside
   `ENVIRONMENT=local/test` and on Azure hosting.

## Options considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Tenant claim from the IdP, roles in our DB (chosen) | Microsoft's documented pattern; no service on the sign-in path; role changes take effect at once | Tenant attribute in access tokens unconfirmed on Learn; one tenant per user | Accepted; fallback documented |
| Token exchange: control plane mints our own platform token | Full control of claims; works without IdP features | We'd run a token issuer (Learn: antipattern); catalog holds user IDs | Unneeded complexity for the pilot |
| Custom claims provider from day one | Claims from our data at issuance | Public endpoint on the sign-in path, 2 s timeout, more moving parts | Kept as the fallback |
| Roles as Entra app roles | No role tables to read | Role changes wait for a new token; customers can't manage roles in our app | Learn notes the flexibility cost |
| Per-stamp hostnames with a sign-in lookup | Simple edge | Needs a user → tenant table outside RLS in each stamp | New non-negotiable exception |
| Venue in every token (re-issue on switch) | No venue in requests | Needs our own issuer or per-venue claims; awkward switching | Not supported by the chosen IdP pattern |

## Consequences

- Good:
  - The tenant is never taken from a request.
  - There's one familiar sign-in path, and no custom issuer in production.
  - Roles live in RLS-protected tables.
  - Every request is checked against the stamp's own data.
- Bad / costs:
  - Every request reads `app_user` and `role_assignment`: two small indexed queries.
  - The tenant attribute must be set correctly at invite. A wrong attribute yields 403s, not data
    exposure, because the user won't exist in that tenant.
  - Users in two customer organisations are not supported yet.
  - Approval events are published after the approval commits, with no outbox. A failed publish
    leaves the approval recorded and the response says so; `POST /drafts/{id}/approval/publish`
    re-sends it, and the executor is idempotent on `approval_id`.
- Follow-up work:
  - **P0-13 / first Entra tenant:**
    - confirm the custom attribute reaches access tokens for the API, otherwise switch to the claims
      provider;
    - set `accessTokenAcceptedVersion: 2`;
    - write the runbook for inviting a pilot user.
  - **P0-11:** the API's Key Vault set rights for venue OAuth secrets.
  - **P0-12:** entitlement cache.
  - **Stamp #2:** APIM tenant → stamp routing.
  - **Later:** in-app invites via Graph, multi-tenant users (tenant selection), and an outbox if
    publish failures matter.

## Revisit when

- A second stamp is built.
- The tenant attribute can't reach access tokens.
- A user needs access to more than one customer organisation.
- A dedicated or regional stamp needs its own IdP.
- Approval event loss is seen in practice.
