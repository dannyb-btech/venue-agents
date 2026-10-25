# 0007. Postgres flexible server with row-level security and pgvector

- **Status:** Accepted
- **Date:** 2026-10-23
- **Deciders:** CTO

## Context

Data is relational (venues, guests, reservations, sales, shifts, drafts, approvals). Microsoft's multitenant
guidance supports shared Postgres with RLS for tenant isolation, PgBouncer for connections, Entra auth, and
elastic clusters for scale-out ([Postgres in multitenant solutions](https://learn.microsoft.com/azure/architecture/guide/multitenant/service/postgresql)).
Retrieval needs (menus, past proposals, supplier docs) are modest.

## Decision

- Azure Database for PostgreSQL flexible server, one per stamp, Australia East.
- `tenant_id` on every tenant table, **in every primary and foreign key**, with RLS on `app.tenant_id`.
- pgvector in the same server for embeddings; move to Azure AI Search only if volume or features demand it.
- Separate global catalog database for the control plane.

## Consequences

- One familiar data store; isolation enforced in the database, not just app code.
- Keys including tenant_id add a little friction but enable elastic-cluster sharding later (ADR-0008).

## Revisit when

A stamp's database nears limits (convert to elastic cluster) or retrieval quality needs hybrid search.
