Task P0-11: infrastructure as code. Details: docs/build/phase-0-foundations.md, .claude/rules/infra.md,
ADR-0008, ADR-0010, docs/architecture/scale-plan.md.

Hard rule: write and validate Bicep only. Do not run any deployment, what-if against a subscription, or
any command that creates or changes Azure resources.

- Use the azure-researcher subagent (Microsoft Learn + Azure MCP Bicep schema tool) for current resource
  types and API versions: Container Apps, Postgres flexible server (pgvector), Key Vault, Service Bus,
  Storage, Durable Task Scheduler, App Insights/Log Analytics, API Management (AI gateway policies),
  Front Door, App Configuration, Foundry resource and Claude deployments.
- infra/bicep/global and infra/bicep/stamp modules; infra/bicep/main.bicep composing them;
  params: canary.bicepparam and general-01.bicepparam (Australia East; Foundry in a Claude-supported region).
- Managed identities, RBAC, private endpoints for data services, diagnostics to Log Analytics.
- GitHub Actions workflow for what-if and deploy with a manual approval environment (for humans to run).

Done when az bicep build and az bicep lint are clean and each stamp is fully described by its params file.
architect-reviewer, tick, /update-memory, commit.
