# 0011. Integration access routes for the OR stack

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO, hospitality partner

## Context

OR uses Google Workspace, SevenRooms, Toast, Tanda, Xero, Webflow, Meta, Stripe and ANZ. Access varies:
Toast standard API access is read-only, needs RMS Essentials or higher, and its support article says it's
US-first ([standard API access](https://doc.toasttab.com/doc/devguide/devApiAccessUserGuide.html)); writes
need the partner program. SevenRooms issues API credentials via its partnership form. Xero has tiered
developer pricing by connections and data egress since March 2026 ([pricing](https://developer.xero.com/pricing)).
Gmail restricted scopes need Google's security assessment for broad distribution. ANZ has no direct SME API.

## Decision

| System | Route |
| --- | --- |
| Google Workspace | OAuth per tenant; internal (single-workspace) app for OR first; security assessment in phase 3 |
| SevenRooms | Partner-form API credentials per venue group; exports as fallback |
| Toast | Standard API (read-only) if available to OR, else scheduled data exports via SFTP; partner program once ≥2 venues |
| Tanda | API v2 OAuth (roster, timesheet, cost scopes); draft rosters only, humans publish |
| Xero | Accounting + AU payroll API; cache aggressively to control egress cost |
| Stripe | Restricted keys per tenant; payment links only on approval |
| Webflow / Meta | Read + draft; publishing manual initially; Meta app review needed for publishing |
| ANZ | Xero bank feed first; CDR via an accredited intermediary only if needed |

Access requests for Toast and SevenRooms go in during week 1 (task P0-13).

## Revisit when

Toast access for OR is confirmed either way, or a partner program is approved.
