-- P0-04: add the control plane's local logins to an existing local volume (init.sql creates them
-- on a fresh one). Idempotent. Local-only placeholder passwords, like va_app_local.
--   docker compose exec -T postgres psql -U app -d postgres < infra/local/postgres/control-plane-roles.sql
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'va_catalog') THEN
        CREATE ROLE va_catalog NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'va_catalog_local') THEN
        CREATE ROLE va_catalog_local LOGIN PASSWORD 'va_catalog_local' IN ROLE va_catalog;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'va_provision') THEN
        CREATE ROLE va_provision NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'va_provision_local') THEN
        CREATE ROLE va_provision_local LOGIN PASSWORD 'va_provision_local' IN ROLE va_provision;
    END IF;
END
$$;
