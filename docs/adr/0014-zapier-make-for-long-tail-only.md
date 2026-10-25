# 0014. Zapier / Make only for the long tail

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO, hospitality partner

## Context

Venues use many minor tools. Building a connector for each is expensive; no-code tools cover them quickly
but add a dependency, cost per task, and weaker tenant controls.

## Decision

Core systems (the table in ADR-0011) get first-party connectors. Anything else may be bridged with
Zapier or Make, called from the platform via webhooks, with the same draft → approval → executor flow.
Agents never depend on a no-code tool for core data.

## Revisit when

A long-tail system appears at more than ~10% of venues: build a first-party connector.
