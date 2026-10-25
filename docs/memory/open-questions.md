# Open questions

Questions for the founders/partners. Answer inline with date; turn decisions into ADRs.

## Business

- [ ] Is OR on Toast RMS Essentials or higher, and can it get standard API access in Australia?
- [ ] Who at OR approves events emails, orders and rosters day to day?
- [ ] What labour % and wine GP targets should agents work to at OR?
- [ ] Pricing model: setup fee + monthly per venue, or per agent?
- [ ] Does OR run payroll in Tanda or Xero (both are in the stack)?
- [ ] Commercial terms for using OR as testimonial and case study.
- [ ] Product/company name (repo is called `venue-agents` as a placeholder).

## Technical

- [ ] Claude quotas (tokens/min) available on Foundry for our subscription and regions.
- [x] Current Agent Framework Anthropic package versions and stability (check at P0-08).
      2026-10-25: `agent-framework-core` 1.23.0 (GA); `agent-framework-anthropic` 1.0.0b261022 and
      `agent-framework-durabletask` 1.0.0b260922 (prerelease); `durabletask(-azuremanaged)` 1.12.0.
      Re-check at P0-08 (weekly releases). Core GA is ADR-0004's revisit trigger: review it before P0-08.
- [ ] Does `AnthropicFoundryClient` support Entra ID / managed identity, and can it target an APIM URL
      (or do we use `AnthropicClient(base_url=...)`)? Learn documents API keys only. Check at P0-08.
- [ ] Learn now documents a `microsoft-opentelemetry` distro (`use_microsoft_opentelemetry`) that looks
      like the successor to `azure-monitor-opentelemetry` (pinned 1.8.11 in P0-02). Check its maturity
      before P0-11; the swap is isolated in `va_core.telemetry._configure_azure_monitor`.
- [ ] Do Foundry Agent Service prompt agents support Claude? (Not relied on; confirm before P3-07.)
- [ ] Azure subscription for the project: pay-as-you-go (Claude on Foundry requires a paid subscription,
      not free trial, credit-only or CSP).

## Access requests (P0-13) — owner, requested date, status

| Item | Owner | Requested | Status |
| --- | --- | --- | --- |
| Toast standard API / SFTP exports | | | |
| SevenRooms API credentials | | | |
| Google Cloud project + OAuth consent | | | |
| Xero developer app | | | |
| Tanda API app/token | | | |
| Foundry Claude deployments (dev) | | | |
