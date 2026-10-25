# Integrations (connector catalogue)

Every system is reachable; Toast and SevenRooms gate access, and Toast is read-only without a partnership
(ADR-0011). Update each row as connectors are built: scopes, rate limits, sync cadence, write paths.

| System | Used for | Access route | Read / write | Agents | Sync | Gotchas | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Google Workspace (Gmail, Drive) | Enquiries, client history, docs | Google APIs, OAuth per tenant | Read + draft; send via executor after approval | Events, all | Gmail history API incremental; Drive changes API | Restricted Gmail scopes need security assessment before wide rollout; OR runs as internal app first | Not started |
| SevenRooms | Reservations, events, guest CRM | API credentials via partnership form (client id/secret, venue group id) | Read; writes depend on granted scopes | Events, Marketing | `updated_since` polling | Approval takes time; confirm region endpoint | Requested? |
| Toast | POS sales, stock, labour | Standard API access (read-only, RMS Essentials+) or partner program; fallback SFTP data exports | Read only | Wine Buyer, Office Manager | Hourly orders/stock; nightly export fallback | Standard access US-first, confirm for OR; per-location requests | Requested? |
| Tanda | Rosters, timesheets, leave, costs | API v2 OAuth: roster, timesheet, cost, user, leave scopes | Read + draft rosters (unpublished); publish stays human | Office Manager | Webhooks/polling | Award interpretation via `show_costs`; rate limits | Not started |
| Xero | Bookkeeping, payroll, bills | Accounting + AU payroll API, OAuth | Read + draft bills/invoices | Office Manager, Wine Buyer | Webhooks + incremental | Tiered developer pricing by connections and egress; cache | Not started |
| Stripe | Deposits | Restricted keys per tenant; webhooks | Read; payment links via executor | Events | Webhooks | Keep keys minimal | Not started |
| Webflow | Website | Data API | Read + draft CMS items | Marketing | On demand | Publishing manual at first | Phase 3 |
| Meta (IG, Facebook) | Content, ads | Graph + Marketing APIs | Draft posts; ads read-only | Marketing | On demand | App review for publishing | Phase 3 |
| ANZ | Banking | Xero bank feed; CDR intermediary only if needed | Read | Office Manager | Via Xero | No direct SME API | Via Xero |

Long tail: Zapier/Make via webhooks only (ADR-0014).

## Per-connector checklist

Scopes documented · per-tenant credentials from Key Vault · timeouts/retries/Retry-After · recorded
fixtures · isolation test · sync cursor · executor handlers (if writes) · rate-limit notes.
