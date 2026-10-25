# 0008. Deployment stamps + global control plane, designed for thousands of venues

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO, founders

## Context

Goal: scale to thousands of venues. A single shared stack is estimated to handle ~200–300 venues before
Claude quota at peak (Sunday night / Monday morning roster and order runs) and then Postgres become limits.
Microsoft's guidance: the Deployment Stamps pattern with a global control plane (tenant catalog, placement,
onboarding), at least two stamps from early on, stamps defined as IaC
([Deployment Stamps](https://learn.microsoft.com/azure/architecture/patterns/deployment-stamp),
[control planes](https://learn.microsoft.com/azure/architecture/guide/multitenant/approaches/control-planes)).
Postgres elastic clusters with row-based sharding by tenant ID scale to very large tenant counts
([sharding models](https://learn.microsoft.com/azure/postgresql/elastic-clusters/concepts-elastic-clusters-sharding-models)).

## Decision

- Shared multi-tenant **stamps** (~250 venues each as a planning figure, re-set from measured load).
- **Global layer:** Front Door routing by venue, tenant catalog (venue → stamp, region, plan,
  entitlements), onboarding/offboarding/move-venue workflows, API Management AI gateway with a backend
  pool of Claude deployments across Foundry resources and regions, central analytics store.
- **Per stamp:** Container Apps environment, Postgres (→ elastic cluster as it grows), Key Vault,
  Service Bus, Blob, Durable Task Scheduler, App Insights.
- **Build now:** tenant_id in all keys; catalog + routing from release one; whole stamp in Bicep; two stamps
  early (canary for OR/internal, general for clients from client two); venue_id on all telemetry/cost;
  deployment rings + feature flags; a move-venue tool.
- **Scale-out triggers:** stamp near ~250 venues or Postgres >60% CPU at peak → new stamp; Claude quota
  >70% at peak → add deployment/region and request quota; isolation demand → dedicated stamp (premium);
  AU-processing demand → AU-processing stamp (ADR-0013).

## Consequences

- Near-linear scaling and contained blast radius; progressive rollouts.
- More infrastructure and cost than one stack; cross-stamp reporting needs the analytics store.

## Revisit when

Measured per-stamp capacity differs substantially from the planning figure.
