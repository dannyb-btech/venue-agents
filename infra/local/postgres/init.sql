-- Runs once when the local Postgres volume is first created (docker-entrypoint-initdb.d).
-- Stamp database (POSTGRES_DB, default venue_agents) already exists; add the global catalog
-- database and enable pgvector in both. Tables, roles and RLS come from Alembic (P0-03).

CREATE DATABASE catalog;

\connect catalog
CREATE EXTENSION IF NOT EXISTS vector;

\connect :"POSTGRES_DB"
CREATE EXTENSION IF NOT EXISTS vector;
