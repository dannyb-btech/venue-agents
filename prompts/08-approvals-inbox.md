Task P0-10: approvals inbox v0. Details: docs/build/phase-0-foundations.md, .claude/rules/frontend.md, ADR-0006.

Key points:
- Next.js (App Router) + TypeScript strict in apps/venue-app with pnpm; generated client from the
  Platform API OpenAPI spec.
- Screens: sign-in, pending drafts list (newest first, grouped by agent), draft detail (what it will do,
  to whom, why, sources), edit-then-approve, reject with reason.
- Mobile-first: comfortable on a phone at night; fast on 4G; accessible.
- Playwright tests for approve and reject against a mocked API.
- Keep styling simple and clean for now; design pass comes later.

/verify, tick, /update-memory, commit.
