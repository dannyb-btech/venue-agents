# 0009. Python for backend services, TypeScript (Next.js) for the venue app

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO

## Context

Agent Framework and the Durable Task SDK both support Python and .NET. Python has the best coverage
of vendor SDKs and data tooling, and keeps one language across agents, connectors and APIs.

## Decision

- Python 3.12 for platform API, control plane, agent runtime, connectors, sync workers, executor.
  `uv` workspace, FastAPI, Pydantic v2, SQLAlchemy + Alembic, pytest, ruff, pyright.
- Next.js + TypeScript for the venue app and admin console, using a generated API client.

## Options considered

| Option | Why not chosen |
| --- | --- |
| .NET throughout | Strong Azure fit, but Python is better for agents/data and vendor SDKs |
| TypeScript backend | Agent Framework and Durable Task SDK support is weaker |

## Revisit when

Agent Framework features we need land only in .NET.
