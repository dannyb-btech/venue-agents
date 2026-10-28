# Architecture decision records

Every decision that changes architecture, a vendor, a data flow or a non-negotiable gets an ADR.
Create one with `/adr <title>`. Template: `0000-template.md`.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-azure-primary-cloud.md) | Azure is the primary cloud (Australia East) | Accepted |
| [0002](0002-claude-on-foundry-and-data-residency.md) | Claude via Foundry Global Standard; data at rest in AU, inference offshore, disclosed | Accepted |
| [0003](0003-model-tiering-and-ai-gateway.md) | Haiku/Sonnet/Opus tiering behind an APIM AI gateway | Accepted |
| [0004](0004-agent-framework-and-durable-execution.md) | Microsoft Agent Framework + Durable Task on Container Apps; hosted agents decided in phase 3 | Accepted |
| [0005](0005-mcp-connectors-read-draft-only.md) | MCP connectors read + draft only; executor holds write scopes | Accepted |
| [0006](0006-human-approval-for-side-effects.md) | Human approval for every external side effect | Accepted |
| [0007](0007-postgres-rls-and-pgvector.md) | Postgres flexible server, RLS, tenant_id in keys, pgvector | Accepted |
| [0008](0008-deployment-stamps-for-thousands-of-venues.md) | Deployment stamps + global control plane for thousands of venues | Accepted |
| [0009](0009-python-backend-typescript-frontend.md) | Python backend, Next.js/TypeScript venue app | Accepted |
| [0010](0010-bicep-iac-and-two-stamps.md) | Bicep for all infra; Claude Code never deploys; two stamps | Accepted |
| [0011](0011-integration-access-routes.md) | Integration access routes for the OR stack | Accepted |
| [0012](0012-pay-and-award-calculations-in-code.md) | Pay/award/labour maths in code, never the model | Accepted |
| [0013](0013-model-provider-abstraction-au-residency.md) | Model provider abstraction; Bedrock AU for residency | Proposed |
| [0014](0014-zapier-make-for-long-tail-only.md) | Zapier/Make only for the long tail | Accepted |
| [0015](0015-webflow-marketing-site.md) | Marketing site on Webflow | Proposed |
| [0016](0016-direct-foundry-calls-local-dev-only.md) | Direct Foundry calls in local development only; deployed stamps use APIM | Accepted |
| [0017](0017-control-plane-catalog-and-stamp-provisioning.md) | Control plane catalog (no RLS, `va_catalog`), `va_provision` in stamps, placement, Entra DB logins | Accepted |
| [0018](0018-run-envelopes-and-connector-framework.md) | Signed run envelopes (HS256 per stamp), connector framework, `<system>_<verb>_<noun>` tool names | Accepted |
| [0019](0019-venue-user-identity-and-tenant-resolution.md) | Venue users: Entra External ID tokens with a tenant claim, roles in the stamp DB, venue selector authorised per request, one stamp until two | Accepted |
