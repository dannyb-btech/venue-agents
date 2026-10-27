-- Runs once when the local Postgres volume is first created (docker-entrypoint-initdb.d).
-- Stamp database (POSTGRES_DB, default venue_agents) already exists; add the global catalog
-- database and enable pgvector in both. Tables, grants and RLS come from Alembic (P0-03, P0-04).
-- On an existing volume, run infra/local/postgres/control-plane-roles.sql instead of recreating it.
--
-- POSTGRES_USER (`app`) is a superuser: it bypasses RLS, so it is only the migration/admin login
-- (DATABASE_ADMIN_URL). Runtime code logs in as `va_app_local`, a member of `va_app` (no BYPASSRLS).
-- Local-only placeholder password, like `app:app`; deployed stamps use Entra (managed identity) logins.

CREATE DATABASE catalog;

CREATE ROLE va_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE va_app_local LOGIN PASSWORD 'va_app_local' IN ROLE va_app;

-- Control plane (ADR-0017): va_catalog in the catalog database, va_provision in the stamp.
CREATE ROLE va_catalog NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE va_catalog_local LOGIN PASSWORD 'va_catalog_local' IN ROLE va_catalog;
CREATE ROLE va_provision NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE va_provision_local LOGIN PASSWORD 'va_provision_local' IN ROLE va_provision;

\connect catalog
CREATE EXTENSION IF NOT EXISTS vector;

-- The entrypoint doesn't pass POSTGRES_DB as a psql variable; read it from the environment.
\getenv stamp_db POSTGRES_DB
\connect :"stamp_db"
CREATE EXTENSION IF NOT EXISTS vector;
