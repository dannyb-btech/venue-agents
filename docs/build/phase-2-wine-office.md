# Phase 2 — Wine Buyer + Office Manager (weeks 8–12)

Goal: three agents live at OR; cost per run measured.

- [ ] **P2-01 Toast connector** — deps: P0-06, Toast access decision — skill: `/new-connector toast`
  - Standard API read (orders, menus, stock, labour) per location, or SFTP export ingestion fallback.
  - Accept: `sale_line`, `menu_item`, `stock_level` populated for OR; tests + isolation.

- [ ] **P2-02 Supplier price lists + Xero bills** — deps: P0-07
  - Ingest supplier price lists (Drive, varied formats) into `supplier`, `price_list_item`; Xero bills read;
    `xero_draft_bill` draft tool.
  - Accept: parser tests on sample lists; unparseable files flagged for a human.

- [ ] **P2-03 Wine Buyer agent** — deps: P2-01, P2-02 — skill: `/new-agent wine-buyer`
  - Weekly: sales velocity and stock → purchase order drafts per supplier; tasting notes for slow sellers;
    list-price suggestions with GP target from `agent_config`. Order sending via executor (email to supplier).
  - Accept: evals baseline; quantities computed in code, explained by the agent.

- [ ] **P2-04 Labour cost engine** — deps: P0-07, P2-01 — ADR-0012
  - Code computes labour % by hour/day from Tanda costs (award interpretation) and Toast hourly sales;
    validates draft rosters against targets and constraints (availability, leave, max hours).
  - Accept: unit tests with fixed scenarios; no model involvement in numbers.

- [ ] **P2-05 Tanda roster drafts** — deps: P0-07
  - `tanda_draft_roster` (unpublished roster); executor never publishes; manager publishes in Tanda.
  - Accept: tests confirm no publish endpoint is called anywhere.

- [ ] **P2-06 Office Manager agent** — deps: P2-04, P2-05 — skill: `/new-agent office-manager`
  - Weekly before roster lock: Opus proposes roster arrangement to target; engine validates; variance
    report (rostered vs actual vs sales); payroll exceptions from Xero/Tanda (summaries only).
  - Accept: evals; every roster draft passes engine validation before reaching the inbox.

- [ ] **P2-07 Cost per run + gateway limits** — deps: P0-08
  - APIM per-venue token limits; cost per agent run dashboard (App Insights workbook); budget alerts.
  - Accept: costs in `usage_cost` reconcile with gateway metrics on a test day.

- [ ] **P2-08 Security review** — `security-reviewer` across phase 2; payroll data minimisation check.
