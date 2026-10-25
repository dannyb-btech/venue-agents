-- Runs once when the local Postgres volume is first created (docker-entrypoint-initdb.d).
-- Stamp database (POSTGRES_DB, default venue_agents) already exists; add the global catalog
-- database and enable pgvector in both. Tables, grants and RLS come from Alembic (P0-03).
--
-- POSTGRES_USER (`app`) is a superuser: it bypasses RLS, so it is only the migration/admin login
-- (DATABASE_ADMIN_URL). Runtime code logs in as `va_app_local`, a member of `va_app` (no BYPASSRLS).
-- Local-only placeholder password, like `app:app`; deployed stamps use Entra (managed identity) logins.

CREATE DATABASE catalog;

CREATE ROLE va_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE va_app_local LOGIN PASSWORD 'va_app_local' IN ROLE va_app;

\connect catalog
CREATE EXTENSION IF NOT EXISTS vector;

-- The entrypoint doesn't pass POSTGRES_DB as a psql variable; read it from the environment.
\getenv stamp_db POSTGRES_DB
\connect :"stamp_db"
CREATE EXTENSION IF NOT EXISTS vector;
