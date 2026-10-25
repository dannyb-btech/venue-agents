# 0004. Microsoft Agent Framework + Durable Task on Container Apps; hosted agents deferred

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO

## Context

Microsoft's recommended default for agents is Microsoft Agent Framework, optionally hosted in Foundry
Agent Service. Agent Framework supports Claude on Foundry (and Bedrock/Vertex) through its Anthropic
client ([Anthropic provider](https://learn.microsoft.com/agent-framework/integrations/by-component/model-providers/anthropic)),
and its durable extension runs agents as deterministic Durable Task orchestrations with checkpointing and
zero-compute waits for human approval
([durable extension](https://learn.microsoft.com/agent-framework/hosting/durable-extension)).
Foundry Agent Service separates sessions by caller identity, but separating venues stays our job, and
customer-managed key coverage for hosted agents is limited.

## Decision

- Build every agent with **Microsoft Agent Framework (Python)** using the Anthropic-on-Foundry client.
- Run them as **durable orchestrations** (Agent Framework durable extension, Durable Task Scheduler)
  on **Azure Container Apps**.
- Use Foundry for models, evaluation and tracing from day one; Foundry Toolbox optional in phase 2.
- **Foundry Agent Service hosted agents:** decide in phase 3 (task P3-07). Moving is a deployment change,
  not a rewrite, because agents are Agent Framework code either way.

## Options considered

| Option | Why not chosen |
| --- | --- |
| Own durable workflows calling Claude directly | Diverges from Microsoft's direction; more glue code |
| Foundry hosted agents from day one | Tenant isolation and CMK gaps; less portable to Bedrock |
| Claude Agent SDK | Strong agent loop but less native to Azure durable hosting and Foundry eval |

## Consequences

- Aligned with Microsoft guidance; durable approvals are cheap; portable across model providers.
- Agent Framework's Anthropic packages are **prerelease**: pin versions, expect API churn.

## Revisit when

Agent Framework reaches GA, or hosted agents gain per-tenant isolation and full CMK support (P3-07).
