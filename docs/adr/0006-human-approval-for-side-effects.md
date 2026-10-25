# 0006. Human approval for every external side effect

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO, founders

## Context

Wrong emails, orders or rosters cost bookings, money and staff trust; rosters and pay carry legal risk.
Managers need to stay the decision-makers, and the approvals inbox is also the product's main surface.

## Decision

No autonomous send, order, pay, publish, roster publish or payroll change in phases 0–3. Every draft goes
to the approvals inbox showing what it will do, to whom, why, and the source data. Approve, edit-then-approve
or reject (with reason). Approvals are recorded with user, time, decision and edits.

## Consequences

- Safe pilot; rich feedback (edits and rejections) to improve prompts and evals.
- Value depends on managers approving promptly: reminders and good mobile UX matter.

## Revisit when

A draft type has a long record of approvals with no edits; propose auto-approval for it via a new ADR,
venue opt-in only.
