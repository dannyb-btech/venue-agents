# 0003. Model tiering behind an API Management AI gateway

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO

## Context

Agent work ranges from cheap classification to heavy reasoning (roster optimisation). Costs must be
attributed per venue for billing, and Foundry rate limits are per deployment/region. API Management's
AI gateway supports per-consumer token limits, backend pools with circuit breakers, and usage metrics
([AI gateway capabilities](https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities)).

## Decision

- **Haiku** for triage/classification, **Sonnet** for most drafting, **Opus** only where configured
  (roster optimisation, complex reasoning). Prompt caching for stable instructions and venue context.
- All model calls go through **API Management** (AI gateway): per-venue token limits keyed on venue ID,
  cost attribution, and (from phase 3) a backend pool across several Foundry deployments and regions.
  Local development only may call a Foundry dev deployment directly (ADR-0016).
- Model names are configuration, not code (`MODEL_TRIAGE`, `MODEL_DRAFT`, `MODEL_REASONING`).

## Consequences

- Per-venue cost visibility from day one; resilience to quota limits later.
- Extra hop and an APIM instance to run.

## Revisit when

Foundry's own gateway features cover per-tenant limits and pooling, or model pricing shifts the tiers.
