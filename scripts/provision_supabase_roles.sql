-- =============================================================================
-- WealthSail Supabase dedicated runtime login provisioning
-- =============================================================================
-- Run once as the database owner in Supabase SQL Editor / direct psql.
-- Idempotent: safe to re-run after a `db reset` or whenever roles drift.
--
-- ROLE MODEL (matches backend.common.database.verify_runtime_login_boundary):
--   * wealthsail_api        NOLOGIN NOBYPASSRLS  — RLS-restricted domain role
--     (created by migrations 009/013; the API executes every statement under
--     SET LOCAL ROLE wealthsail_api so row-level policies always apply)
--   * wealthsail_worker     NOLOGIN BYPASSRLS    — trusted Worker role
--     (also created by migrations; the Worker pool activates it via
--     SET ROLE wealthsail_worker, and downgrades to wealthsail_api for
--     tenant-scoped report transactions — see migrations/013 GRANT)
--   * wealthsail_api_login    LOGIN NOBYPASSRLS  — API  connection identity
--     (can SET ROLE wealthsail_api ONLY; must never reach the Worker role)
--   * wealthsail_worker_login LOGIN NOBYPASSRLS  — Worker connection identity
--     (can SET ROLE wealthsail_worker AND wealthsail_api for downgrades)
--
-- WHY NOT a LOGIN + BYPASSRLS worker account: verify_runtime_login_boundary
-- rejects any session login whose rolbypassrls is true ("runtime database
-- login must not be superuser/BYPASSRLS"). BYPASSRLS must live on the
-- NOLOGIN wealthsail_worker role, activated only after the trusted Worker
-- process switches to it.
--
-- Passwords are set at the end via placeholders. NEVER commit real passwords
-- to Git/SQL history: inject them through the platform's secret channel or
-- substitute locally and keep the file untracked.
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wealthsail_api_login') THEN
        CREATE ROLE wealthsail_api_login
            LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wealthsail_worker_login') THEN
        CREATE ROLE wealthsail_worker_login
            LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;

-- Reassert attributes on rerun; provisioning is idempotent and repairs drift.
ALTER ROLE wealthsail_api_login
    NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE wealthsail_worker_login
    NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- SET-role memberships (WITH SET TRUE is mandatory: PostgreSQL defaults the
-- SET option to false for roles created via GRANT by non-superusers, which
-- would make SET ROLE fail with "permission denied").
GRANT wealthsail_api TO wealthsail_api_login WITH SET TRUE, INHERIT FALSE;
GRANT wealthsail_worker TO wealthsail_worker_login WITH SET TRUE, INHERIT FALSE;
-- The Worker downgrade path (worker -> api) is granted by migrations/013
-- (GRANT wealthsail_api TO wealthsail_worker) and is transitive here.

-- The API must never escalate to Worker.
REVOKE wealthsail_worker FROM wealthsail_api_login;
-- Direct Worker->API grant is removed as hygiene; the transitive path via
-- wealthsail_worker remains the sanctioned downgrade route.
REVOKE wealthsail_api FROM wealthsail_worker_login;

-- Remove the old explicit shared-login grants. `postgres` remains a platform
-- administrator, but it must never appear in either runtime connection URL.
REVOKE wealthsail_api FROM postgres;
REVOKE wealthsail_worker FROM postgres;

-- ---------------------------------------------------------------------------
-- Connectivity and schema access (idempotent; migrations grant the granular
-- table privileges per schema, so no blanket ALL TABLES grant is needed —
-- that would silently widen least-privilege boundaries).
-- ---------------------------------------------------------------------------
GRANT CONNECT ON DATABASE postgres TO wealthsail_api_login, wealthsail_worker_login;

-- Schema USAGE for the runtime roles (GRANT is idempotent; existing grants
-- from migrations are unaffected). Include every schema the backend touches.
GRANT USAGE ON SCHEMA identity, ledger, market, reporting, workflow,
    reference, audit, risk, research
    TO wealthsail_api, wealthsail_worker;

-- ---------------------------------------------------------------------------
-- Passwords (REPLACE the placeholders; do not commit real secrets)
-- ---------------------------------------------------------------------------
-- ALTER ROLE wealthsail_api_login    WITH PASSWORD '<api-strong-password>';
-- ALTER ROLE wealthsail_worker_login WITH PASSWORD '<worker-strong-password>';
-- ALTER ROLE wealthsail_worker       WITH PASSWORD '<worker-pool-password>';  -- only if a direct Worker pool login is ever required (not recommended)

-- ---------------------------------------------------------------------------
-- Boundary verification: mirrors verify_runtime_login_boundary() checks so a
-- misconfigured role matrix fails loudly at provisioning time.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    api_can_api       BOOLEAN;
    api_can_worker    BOOLEAN;
    worker_can_api    BOOLEAN;
    worker_can_worker BOOLEAN;
    api_bypassrls     BOOLEAN;
    worker_bypassrls  BOOLEAN;
BEGIN
    SELECT pg_has_role('wealthsail_api_login', 'wealthsail_api', 'SET'),
           pg_has_role('wealthsail_api_login', 'wealthsail_worker', 'SET'),
           pg_has_role('wealthsail_worker_login', 'wealthsail_api', 'SET'),
           pg_has_role('wealthsail_worker_login', 'wealthsail_worker', 'SET'),
           rolbypassrls,
           (SELECT rolbypassrls FROM pg_roles WHERE rolname = 'wealthsail_worker_login')
      INTO api_can_api, api_can_worker, worker_can_api, worker_can_worker,
           api_bypassrls, worker_bypassrls
      FROM pg_roles WHERE rolname = 'wealthsail_api_login';

    IF NOT api_can_api OR api_can_worker OR NOT worker_can_api OR NOT worker_can_worker
       OR api_bypassrls OR worker_bypassrls THEN
        RAISE EXCEPTION
            'RUNTIME_ROLE_BOUNDARY_FAILED api->api=% api->worker=% worker->api=% worker->worker=% api_bypass=% worker_bypass=%',
            api_can_api, api_can_worker, worker_can_api, worker_can_worker,
            api_bypassrls, worker_bypassrls;
    END IF;
    RAISE NOTICE 'runtime role boundary OK; now inject two independent passwords';
END $$;
