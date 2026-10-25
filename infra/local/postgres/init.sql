-- Runs once when the local Postgres volume is first created (docker-entrypoint-initdb.d).
-- Stamp database (POSTGRES_DB, default venue_agents) already exists; add the global catalog
-- database and enable pgvector in both. Tables, roles and RLS come from Alembic (P0-03).

CREATE DATABASE catalog;

\connect catalog
CREATE EXTENSION IF NOT EXISTS vector;

-- The entrypoint doesn't pass POSTGRES_DB as a psql variable; read it from the environment.
\getenv stamp_db POSTGRES_DB
\connect :"stamp_db"
CREATE EXTENSION IF NOT EXISTS vector;
