# Venue Agents

AI agents that run hospitality admin (events enquiries, wine buying, rosters and labour, admin),
built on Azure with Claude via Microsoft Foundry. Pilot venue: OR.

This repo is set up for an **agentic build with Claude Code in VS Code**.

## Getting started

1. Install: Git, Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js LTS + pnpm, Docker Desktop,
   Azure CLI (with Bicep), and the Claude Code extension for VS Code.
2. Open this folder in VS Code, open Claude Code, and approve the project MCP servers
   (`azure`, `microsoft-learn`) when prompted. Run `az login` first so the Azure MCP server can sign in.
3. Copy `.env.example` to `.env` (never commit it).
4. Paste `prompts/00-orientation.md` into Claude Code for a read-only walkthrough.
5. Then run `/build-phase 0` (or paste `prompts/01-bootstrap-monorepo.md`) to start building.

## What's where

| Path | Purpose |
| --- | --- |
| `CLAUDE.md` | Project memory Claude Code loads every session: non-negotiables, stack, commands, workflow |
| `.claude/rules/` | Path-scoped rules (Python, agents, connectors, database, infra, frontend, security) |
| `.claude/skills/` | Slash commands: `/build-phase`, `/new-connector`, `/new-agent`, `/adr`, `/verify`, `/update-memory` |
| `.claude/agents/` | Subagents: architect-reviewer, security-reviewer, test-writer, azure-researcher |
| `.claude/settings.json` | Permissions (Azure writes and pushes need approval; `.env` unreadable) and a format hook |
| `.mcp.json` | Project MCP servers: Azure MCP (local), Microsoft Learn (remote) |
| `docs/architecture/` | Architecture, data model, API, agents, integrations, security, scale plan |
| `docs/adr/` | Architecture decision records for every decision made so far |
| `docs/build/` | Roadmap and phase task lists with acceptance criteria |
| `docs/memory/` | Shared memory: progress log, open questions, lessons, glossary |
| `prompts/` | Kickoff prompts for each build session |

Source plan: "Hospitality AI Agents — Build Plan" (Claude Docs).
