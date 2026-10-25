# 0013. Model provider abstraction, with Bedrock AU as the residency option

- **Status:** Proposed
- **Date:** 2026-10-23
- **Deciders:** CTO

## Context

Some clients may require AI processing in Australia. Amazon Bedrock's Australia cross-region inference
keeps Claude processing within Sydney and Melbourne, though new models often reach the AU profile later.
Agent Framework's Anthropic client also supports Bedrock.

## Decision

- All model calls go through one model client in `packages/core` configured per stamp/tenant
  (provider, endpoint, model names). No provider-specific code elsewhere.
- Default provider: Foundry (ADR-0002). For clients needing AU processing, an **AU-processing stamp**
  uses Claude on Bedrock AU cross-region inference (when the first such client signs).

## Consequences

- Residency becomes a configuration and placement choice, not a rewrite.
- A second cloud account, credentials and eval runs per provider when used.

## Revisit when

Foundry offers Claude in Australia (then drop Bedrock), or the first AU-residency client signs.
