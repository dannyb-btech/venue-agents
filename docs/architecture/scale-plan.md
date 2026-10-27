# Scale plan: thousands of venues

Scale by adding identical deployment stamps, each serving a group of venues, with a thin global control
plane routing each venue to its stamp (ADR-0008).

## Target shape

| Layer | Scope | Design |
| --- | --- | --- |
| Edge routing | Global | Front Door routes each venue's requests to its stamp |
| Control plane | Global | Tenant catalog (venue → stamp, region, plan, entitlements); onboarding, offboarding, move-venue workflows |
| AI gateway | Global | APIM backend pool of Claude deployments across Foundry resources/regions; priority routing; circuit breakers honouring Retry-After; per-venue token limits |
| App tier | Per stamp | Container Apps environment with all services |
| Data | Per stamp | Postgres → elastic cluster (row-based sharding by tenant_id); Blob, Key Vault, Service Bus, Durable Task Scheduler |
| Analytics | Global | Stamps publish to a central store for cross-venue reporting |

Planning figure: ~250 venues per stamp (replace with measured capacity). 1,000 venues ≈ 4–5 stamps.

## First constraint: Claude capacity at peak

Sunday night / Monday morning roster and order runs hit Foundry rate limits first.

- Spread load across deployments in several Foundry resources/regions behind the gateway pool.
- Request quota increases a quarter ahead of growth.
- Stagger scheduled runs per venue across a window; run non-urgent work off-peak.
- Prompt caching; Haiku wherever quality allows.
- AU-residency clients on a Bedrock-backed stamp (ADR-0013).

## Build now (phase 0)

- [ ] tenant_id in every table's keys
- [x] Tenant catalog and venue → stamp routing from release one (P0-04, ADR-0017)
- [ ] Whole stamp in Bicep; two stamps early (canary + general)
- [ ] venue_id on every log, trace, token count and cost record
- [ ] Deployment rings, feature flags (App Configuration), entitlements in the catalog
- [ ] Move-venue tool (phase 3)

## Scale-out triggers

| Signal | Action |
| --- | --- |
| Stamp near ~250 venues, or Postgres >60% CPU at peak | Open a new stamp; place new venues there |
| Claude quota use >70% at peak | Add deployment/region to the pool; request quota |
| Group wants isolation or custom terms | Dedicated stamp, premium pricing |
| Client needs AU processing | AU-processing stamp |

## Non-technical limits

Onboarding (self-service connections, templates), partner status (Toast partner program, Google
verification, Xero tiers, SevenRooms partnership), trust (SOC 2 / ISO 27001), support (on-call, tooling
to explain what each agent did and why).
