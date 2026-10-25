# 0016. Direct Foundry calls in local development only

- **Status:** Accepted
- **Date:** 2026-10-25
- **Deciders:** Danny Byra

## Context

ADR-0003 routes every model call through the API Management AI gateway for per-venue token limits,
cost attribution and (later) a backend pool
([AI gateway capabilities](https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities)).
There is no Azure subscription for the project yet, so no APIM instance exists, and developers need to run
agents against Claude before the gateway is deployed (P0-08 requires one manual run against a Foundry dev
deployment). Agent Framework's Anthropic client can target Foundry directly or any Anthropic-compatible
base URL, such as an APIM endpoint
([Anthropic provider](https://learn.microsoft.com/agent-framework/integrations/by-component/model-providers/anthropic)).

## Decision

- When `ENVIRONMENT=local`, `MODEL_GATEWAY_URL` may point straight at a Foundry **dev** deployment.
- Every deployed stamp (canary, general, any later stamp) calls models **only through APIM**. The model
  client in `packages/core` refuses a non-gateway endpoint outside `ENVIRONMENT=local` (enforced from P0-08).
- Local runs use synthetic or anonymised data only, never real venue data, because they bypass the
  gateway's per-venue limits and cost records.
- ADR-0003 otherwise stands unchanged.

## Options considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Gateway only, everywhere | Simplest rule | Blocks agent development until APIM is deployed | Gateway doesn't exist yet |
| Direct calls until the gateway exists, in any environment | Faster pilot start | Deployed stamps would run without per-venue limits or cost attribution | Weakens ADR-0003 where it matters |
| Gateway optional permanently | Least infra | Loses per-venue cost and quota control | Contradicts ADR-0003 |

## Consequences

- Good: agent work (P0-08) can proceed locally before any Azure infra exists.
- Bad / costs: local token spend isn't attributed per venue; a misconfigured deployment could bypass the
  gateway, mitigated by the environment check in the model client.
- Follow-up work: P0-08 adds the environment guard and a test for it.

## Revisit when

APIM is deployed and a local developer gateway (or APIM dev instance) is cheap enough to use from laptops.
