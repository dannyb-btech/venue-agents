# 0001. Azure is the primary cloud

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO, founders

## Context

The CTO is most experienced with Azure. Claude is generally available in Microsoft Foundry, billed on
the Azure invoice, authenticated with Entra ID, and usable from Microsoft Agent Framework. Venue data
should be stored in Australia.

## Decision

Build the platform on Azure, primary region **Australia East**. Use managed PaaS services
(Container Apps, Postgres flexible server, API Management, Key Vault, Service Bus, Blob, Durable Task
Scheduler, Azure Monitor, Front Door).

## Options considered

| Option | Why not chosen |
| --- | --- |
| AWS (Bedrock in-region Claude for AU) | Team less familiar; kept as an alternative model backend (ADR-0013) |
| GCP | No team experience; Claude in-region processing in AU unconfirmed |

## Consequences

- One bill, one identity system, familiar tooling. Claude inference is outside Australia (ADR-0002).

## Revisit when

A large client mandates a different cloud, or Azure pricing materially changes margin.
