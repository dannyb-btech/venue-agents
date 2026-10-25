---
paths:
  - "apps/venue-app/**"
---

# Venue app rules

- Next.js (App Router), TypeScript strict, pnpm. Mobile-first PWA: managers approve on a phone at 11pm.
- The approvals inbox is the product. Each draft shows: what it will do, to whom, why (agent reasoning
  summary), and the source data it used. Approve, edit-then-approve, reject with reason.
- Generated API client from the Platform API OpenAPI spec; never hand-write fetch calls to it.
- Auth via Entra External ID; role checks (owner, manager, approver, viewer) on the server, not just the UI.
- No tenant IDs in URLs or local storage; the session determines the tenant.
- Accessible (keyboard, screen reader labels), fast on 4G; test with Playwright for the approve flow.
