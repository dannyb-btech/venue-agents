# Phase 3 — Productise (months 4–6)

Goal: client two live on stamp 2; case study signed; ready to scale.

- [ ] **P3-01 Second stamp + move-venue tool** — deps: P0-11 — ADR-0008, ADR-0010
  - `general-01` params; deployment rings (canary first); move-venue export/import between stamps.
  - Accept: a test venue moves between two local stamps with data intact and catalog updated.

- [ ] **P3-02 AI gateway backend pool** — deps: P2-07
  - APIM backend pool across Foundry deployments in ≥2 regions; priority routing; circuit breakers
    honouring Retry-After; staggered scheduling window per venue.
  - Accept: load test of a simulated Monday-morning peak for 300 venues stays within quota.

- [ ] **P3-03 Self-service onboarding** — deps: P0-04, P0-05
  - Guided connection flows, venue templates (targets, tone, policies), onboarding checklist in the venue app.
  - Accept: a new venue can connect Google, Tanda and Xero without engineering help.

- [ ] **P3-04 Google restricted-scope verification (human + docs)** — security assessment prep and submission.

- [ ] **P3-05 Admin console** — tenant onboarding, connection health, run traces, prompt versions,
  cost per tenant, audited support impersonation.

- [ ] **P3-06 Admin Assistant + Marketing agents** — skill: `/new-agent` for each; Webflow and Meta connectors.

- [ ] **P3-07 Hosted agents decision** — deps: P3-02 — write an ADR on moving to Foundry Agent Service
  hosted agents (tenant isolation, CMK, cost, portability). Use `azure-researcher` + `architect-reviewer`.

- [ ] **P3-08 Trust readiness** — SOC 2 / ISO 27001 gap list, security overview page, DPA template.

- [ ] **P3-09 Client two (human + build)** — onboard onto `general-01`; OR stays on canary.
