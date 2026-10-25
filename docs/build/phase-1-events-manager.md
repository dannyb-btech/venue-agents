# Phase 1 — Events Manager (weeks 4–7)

Goal: Events Manager live at OR. Gate 2: OR uses drafts weekly.

- [ ] **P1-01 SevenRooms connector** — deps: P0-06, SevenRooms credentials — skill: `/new-connector sevenrooms`
  - Read guests, reservations, events; incremental sync into `guest`, `reservation`.
  - Accept: tests + isolation tests; sync cursor resumes correctly.

- [ ] **P1-02 Enquiry detection and triage** — deps: P0-07
  - Gmail sync → classify new threads (Haiku) into enquiry / not; extract structured `event_enquiry`
    (date, guests, budget, type, contact) with confidence; link to existing guest records.
  - Accept: ≥90% precision on the labelled OR sample; low-confidence items flagged, not dropped.

- [ ] **P1-03 Venue knowledge** — deps: P0-07
  - Ingest event menus, packages, policies (Drive) into `document`/`chunk` with embeddings; retrieval
    tool filtered by tenant.
  - Accept: retrieval tests return the right package for sample queries; isolation test.

- [ ] **P1-04 Events Manager agent: enquiry replies** — deps: P1-02, P1-03 — skill: `/new-agent events-manager`
  - Drafts replies with suitable packages, availability (from SevenRooms), next steps; never invents prices
    or dates; flags uncertainty for the manager.
  - Accept: evals baseline recorded; injection cases pass; drafts render clearly in the inbox.

- [ ] **P1-05 Stripe connector + deposit links** — deps: P0-09 — skill: `/new-connector stripe`
  - Draft payment links attached to an approved reply; executor creates the link only after approval.
  - Accept: idempotent; restricted key scope documented.

- [ ] **P1-06 Past-client outreach run** — deps: P1-01, P1-04
  - Weekly staggered run: find past event clients (SevenRooms + Gmail history) due for outreach; draft
    tailored emails; respect opt-outs and frequency caps.
  - Accept: no client contacted more than the cap; opt-outs honoured in tests.

- [ ] **P1-07 Evals with Foundry evaluation** — deps: P1-04
  - Anonymised OR enquiries (≥20) + injection cases; graders for accuracy, tone, policy adherence;
    results in `evals/events-manager/scores.md`; optional Foundry evaluation run.
  - Accept: repeatable `uv run python -m evals.run events-manager`.

- [ ] **P1-08 Hours-saved tracking** — deps: P0-10
  - Per draft type, estimated minutes saved (configurable), edits and time-to-approve; weekly report endpoint
    and inbox view.
  - Accept: report matches seeded data in tests.

- [ ] **P1-09 Security review + Gate 2 prep** — deps: all above
  - `security-reviewer` on the whole phase; fix blockers; runbook "bad draft sent" drafted.
  - Accept: no Critical/High findings open.
