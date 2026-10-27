# Security, privacy and multi-tenancy

## Tenancy model

Shared stamps with logical isolation: shared app tier, shared database with row-level security,
tenant_id in all keys (ADR-0007, ADR-0008). Dedicated stamps available at a premium. Isolation is tested
continuously (`tests/isolation/`).

## Data residency

Stored data (Postgres, Blob, Key Vault, logs) in Australia East. Claude inference via Foundry Global
Standard runs outside Australia; Anthropic is the data processor (ADR-0002). Disclose as a cross-border
disclosure (APP 8) in the client agreement and privacy policy; minimise data in prompts (no TFNs, bank
details, line-level payroll). AU-processing option via Bedrock AU (ADR-0013).

## Identity and secrets

- Entra External ID for venue users; roles owner, manager, approver, viewer (server-enforced).
- Managed identities between services; RBAC; no keys where Entra auth exists.
- Per-tenant OAuth tokens in Key Vault with minimum scopes; only the executor uses write scopes.

## Agent safety

- Tenant set in code from the authenticated session or signed run envelope, never by a model.
  Connectors accept only a run envelope (HS256 per-stamp key, ≤ 10 min, audience = one connector,
  carrying the agent's tool allow-list) and enforce the allow-list themselves (ADR-0018).
- Inbound email, documents, web content treated as untrusted; injection eval cases per agent.
- No autonomous send/pay/order/publish (ADR-0006). Full audit trail of drafts, edits, approvals, actions.

## Platform hygiene

Private endpoints for Postgres, Key Vault, Foundry; Defender for Cloud; Azure Monitor + App Insights
with OpenTelemetry traces per run (venue_id, run_id); backups with point-in-time restore; Bicep from day
one; secrets scanning in CI; dependency updates weekly.

## Compliance path

Privacy policy + DPA before OR go-live; SOC 2 or ISO 27001 readiness before selling to large groups (P3-08).
