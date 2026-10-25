# Roadmap

Three agents live at OR in 12 weeks; client two on stamp 2 by month 6.

| Phase | When | Goal | Gate |
| --- | --- | --- | --- |
| [0 Foundations](phase-0-foundations.md) | Weeks 1–3 | Monorepo, tenancy, DB, control plane, connector framework, agent runtime, executor, inbox v0, Bicep | **Gate 1:** OR data flowing (read-only) into the canary stamp |
| [1 Events Manager](phase-1-events-manager.md) | Weeks 4–7 | Enquiry replies, past-client outreach, evals, hours-saved tracking | **Gate 2:** OR uses drafts weekly |
| [2 Wine + Office](phase-2-wine-office.md) | Weeks 8–12 | Wine Buyer and Office Manager agents, cost per run | Three agents live at OR |
| [3 Productise](phase-3-productise.md) | Months 4–6 | Second stamp, onboarding, gateway pool, Gmail verification, more agents | **Gate 3:** case study signed, client two live |

## How tasks are written

Each task has an ID (`P<phase>-<nn>`), dependencies, linked docs/ADRs, acceptance criteria and checks.
Tasks marked **(human)** are for the founders/CTO (access requests, contracts, deployments).
Work them with `/build-phase <phase> [task-id]` or the matching prompt in `prompts/`.
Tick tasks (`- [x]`) when acceptance criteria are verified, with a one-line note.
