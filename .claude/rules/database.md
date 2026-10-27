---
paths:
  - "packages/db/**"
  - "apps/control-plane/**"
---

# Database rules

- Postgres flexible server per stamp; separate global catalog database for the control plane.
  The catalog has no RLS: only `va_catalog` reaches it, and it holds no business data (ADR-0017).
  The rules below are for stamp tables.
- Every tenant table: `tenant_id uuid not null`, included in the primary key (`(tenant_id, id)`)
  and in every foreign key. This keeps the door open to elastic-cluster sharding. (ADR-0008)
- Every tenant table gets `ENABLE ROW LEVEL SECURITY` + a policy on `current_setting('app.tenant_id')`
  in the same migration that creates it. App roles never have `BYPASSRLS`.
- Synced rows keep `source_system`, `source_id`, `synced_at`; unique on `(tenant_id, source_system, source_id)`.
- Migrations: Alembic, one concern per migration, reversible, no data-destroying downgrades without review.
- Embeddings: pgvector column on `chunk` with tenant_id; HNSW index; queries always filter by tenant.
- Indexes start with `tenant_id`. Check query plans for anything an agent runs on a schedule.
