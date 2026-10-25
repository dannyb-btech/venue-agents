Tasks P0-04 then P0-12: control plane (tenant catalog, routing, onboarding) and feature flags/entitlements.
Details: docs/build/phase-0-foundations.md, docs/architecture/scale-plan.md, ADR-0008.

Key points:
- Separate catalog database (global) from stamp databases. Run local tests with TWO stamp databases
  so no code assumes a single stamp.
- POST /venues places a venue on a stamp (capacity-aware, placeholder policy: fewest venues in the
  target region and ring) and creates the tenant in that stamp idempotently (onboarding_job tracks steps).
- GET /route returns the stamp for a venue; design it to be cacheable at the edge.
- P0-12: feature flags via Azure App Configuration (local: file-backed implementation behind the same
  interface); entitlements from the catalog; ring per stamp. A flag can enable an agent for one venue.

Ask me before choosing anything that affects pricing or plans. /verify, security-reviewer,
architect-reviewer (this is core scale architecture), tick, /update-memory, commit per task.
