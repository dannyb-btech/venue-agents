# 0012. Pay, award and labour-cost calculations are deterministic code

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO, founders

## Context

A roster that breaches an award or enterprise agreement creates underpayment liability. Language models
are not reliable calculators of award rules, penalty rates or labour percentages.

## Decision

- Award interpretation and costs come from **Tanda** (`show_costs`, award interpretation); labour % is
  computed in code from Tanda costs and Toast hourly sales.
- The Office Manager agent proposes roster arrangements and explains trade-offs; code validates every
  draft roster against costs and constraints before it reaches the inbox.
- Managers publish rosters in Tanda; the venue remains the decision-maker (stated in the client contract).

## Consequences

- Lower legal risk; agents remain useful for arrangement and explanation.

## Revisit when

Never relaxed without legal review.
