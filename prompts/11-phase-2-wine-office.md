Phase 2: Wine Buyer and Office Manager. Work through docs/build/phase-2-wine-office.md with
`/build-phase 2`, in order.

Phase-specific guidance:
- Check docs/memory/open-questions.md for the Toast access decision before P2-01; if standard API isn't
  available, build the SFTP export ingestion path.
- ADR-0012 is absolute: labour %, award costs and order quantities are computed in code. The agents
  arrange, explain and write; they don't calculate. Write the labour cost engine (P2-04) with exhaustive
  unit tests before the Office Manager agent.
- Rosters are drafted unpublished in Tanda; there must be no code path that publishes.
- Payroll data in prompts: summaries only, no line-level pay, TFNs or bank details.
- Ask me for OR's labour % and wine GP targets before P2-03/P2-06.
