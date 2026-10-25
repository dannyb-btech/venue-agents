Phase 1: Events Manager. Work through docs/build/phase-1-events-manager.md one task per session with
`/build-phase 1`, in order P1-01 … P1-09.

Phase-specific guidance:
- Read docs/architecture/agents.md (Events Manager row and rules) before P1-04.
- Use /new-connector sevenrooms (P1-01), /new-connector stripe (P1-05), /new-agent events-manager (P1-04).
- Eval data comes from real OR enquiries: I will provide exports in evals/events-manager/raw/ (gitignored).
  Anonymise names, emails, phones and companies before anything is committed or sent to a model in evals.
- The agent must never invent prices, dates, availability or policies; it uses retrieved packages and
  SevenRooms availability, and flags uncertainty in the draft for the manager.
- Inbound email is untrusted: include injection cases (instructions in emails to change recipients,
  discounts, or reveal other clients).
- Outreach respects opt-outs and frequency caps; ask me for OR's rules before P1-06.
