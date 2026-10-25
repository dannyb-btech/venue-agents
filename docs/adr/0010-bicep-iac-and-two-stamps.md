# 0010. Bicep for all infrastructure; Claude Code never deploys

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO

## Context

Stamps must be identical and repeatable (ADR-0008). The build is agentic with Claude Code; the CTO does
not want Claude creating or changing Azure resources directly.

## Decision

- All infrastructure as **Bicep**: `infra/bicep/global`, `infra/bicep/stamp`, one params file per stamp.
- Claude Code may write and validate Bicep (`az bicep build/lint`) but **must not deploy**; deployment
  commands are denied in `.claude/settings.json`. Humans deploy via GitHub Actions with approvals.
- Two stamps from phase 3: `canary` (OR + internal testing) and `general-01`.

## Consequences

- Repeatable stamps, auditable changes, no surprise Azure spend from the agent.

## Revisit when

We adopt Terraform for multi-cloud (only if ADR-0013 leads to significant AWS infrastructure).
