Phase 3: productise. Work through docs/build/phase-3-productise.md with `/build-phase 3`.

Phase-specific guidance:
- P3-01 and P3-02 are scale architecture: run architect-reviewer and keep docs/architecture/scale-plan.md
  and ADR-0008 in sync.
- P3-02 load test: simulate a Monday-morning peak (300 venues, staggered windows) against a mocked gateway
  with realistic token sizes; report the quota needed per model.
- P3-07: research Foundry Agent Service hosted agents with azure-researcher (tenant isolation, CMK, cost,
  Claude support, portability to Bedrock), then write an ADR with /adr and leave it Proposed for me.
- Never deploy; prepare params and pipeline changes for a human to run.
