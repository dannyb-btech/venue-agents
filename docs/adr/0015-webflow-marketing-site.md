# 0015. Marketing site on Webflow

- **Status:** Proposed
- **Date:** 2026-10-23
- **Deciders:** founders

## Context

The marketing site needs services, the OR case study, pricing and booking a call. Venues (and OR) already
know Webflow; the founders should be able to edit it without engineering.

## Decision

Build the marketing site in Webflow, separate from the platform. Platform apps live on our own domain
under Front Door.

## Revisit when

The site needs deep product integration (self-service signup into the control plane).
