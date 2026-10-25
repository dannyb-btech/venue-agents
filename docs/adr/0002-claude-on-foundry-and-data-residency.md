# 0002. Claude via Microsoft Foundry; data at rest in Australia, inference offshore

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO, founders

## Context

Claude models are generally available in Microsoft Foundry (GA June 2026), with Azure billing and Entra
auth. Claude deployments are offered as Global Standard (US and Swedish regions) and a US data zone;
there is **no Australian region or data zone for Claude** as of October 2026
([region availability](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/models-from-partners#region-availability-by-deployment-type)).
Anthropic is the data processor for prompts and outputs
([hosting comparison](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/claude-models-hosting-comparison)).

## Decision

- Use Claude through Foundry, **Global Standard** deployments, in a Foundry resource in a supported region.
- All stored data (Postgres, Blob, Key Vault, logs) stays in Australia East.
- Disclose cross-border processing to clients (Privacy Act APP 8) in the client agreement and privacy policy.
- Minimise what's sent to the model; never send TFNs, bank details or line-level payroll.
- Re-check Foundry region availability every quarter.

## Options considered

| Option | Why not chosen |
| --- | --- |
| Anthropic API direct | Separate billing/identity; same residency position |
| Bedrock AU cross-region inference | Keeps processing in AU but adds a second cloud; kept as an option (ADR-0013) |
| Data Zone US | No residency benefit for AU clients over Global |

## Consequences

- Fastest path for OR. Some clients may refuse offshore processing; route them per ADR-0013.

## Revisit when

Foundry offers Claude in an Australian region or data zone.
