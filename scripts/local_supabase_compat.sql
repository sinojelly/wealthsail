-- ==============================================================================
-- WealthSail Local Development: Supabase Compatibility Layer
-- ==============================================================================
-- Local Postgres (non-Supabase) does not ship the Supabase auth schema, the
-- `authenticated` role, or the auth.uid() helper that migrations 005/007/008
-- reference. This script creates minimal local stand-ins so the migrations and
-- the backend's tenant_transaction RLS handshake (SET LOCAL ROLE authenticated
-- + set_config('request.jwt.claims', ...)) work identically to Supabase.
--
-- Run as a SUPERUSER (e.g. `psql -U lenovo`):
--     psql -h localhost -p 5433 -U lenovo -d wealthsail_dev -f scripts/local_supabase_compat.sql
-- ==============================================================================

-- 1. The `authenticated` NOLOGIN role used by every RLS policy.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END $$;

-- 2. Allow the application connection role to impersonate `authenticated`
--    inside a transaction (SET LOCAL ROLE authenticated).
GRANT authenticated TO wealthsail_user;
-- The Worker also executes per-tenant report generation through the same RLS
-- handshake (its BYPASSRLS applies only while it stays on its own role).
GRANT authenticated TO wealthsail_worker;

-- 3. Minimal auth schema with auth.uid() reading the injected JWT claims.
CREATE SCHEMA IF NOT EXISTS auth;

CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
    SELECT (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid
$$;

-- 4. Dedicated worker login role. Mirrors the Supabase service_role pattern:
--    the background Worker (scheduler + Outbox consumer) processes rows for
--    ALL tenants, so it bypasses RLS and enforces tenant_id in application
--    code instead. The API role never uses this account.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wealthsail_worker') THEN
        CREATE ROLE wealthsail_worker LOGIN BYPASSRLS PASSWORD 'dev_worker_password_123';
    END IF;
END $$;

GRANT USAGE ON SCHEMA identity, ledger, reference, market, research,
    reporting, risk, workflow, audit TO wealthsail_worker;
GRANT ALL ON ALL TABLES IN SCHEMA identity, ledger, reference, market, research,
    reporting, risk, workflow, audit TO wealthsail_worker;
GRANT ALL ON ALL SEQUENCES IN SCHEMA identity, ledger, reference, market, research,
    reporting, risk, workflow, audit TO wealthsail_worker;
