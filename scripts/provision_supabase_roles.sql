-- =============================================================================
-- WealthSail Supabase dedicated runtime login provisioning
-- =============================================================================
-- Run once as the database owner in Supabase SQL Editor / direct psql.
--
-- This script deliberately creates two different LOGIN identities.  Granting
-- both wealthsail_api and BYPASSRLS wealthsail_worker to one shared `postgres`
-- login is not separation: a compromised API could simply SET ROLE Worker.
--
-- Passwords are intentionally NOT accepted by this file and must never enter
-- Git/SQL history.  After provisioning, set independent generated passwords
-- through the platform's secret channel, then configure:
--   API    DATABASE_URL         -> wealthsail_api_login
--   Worker WORKER_DATABASE_URL  -> wealthsail_worker_login
-- The two URLs, pools and deployment secrets must remain separate.
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

GRANT wealthsail_api TO wealthsail_api_login WITH SET TRUE, INHERIT FALSE;
GRANT wealthsail_worker TO wealthsail_worker_login WITH SET TRUE, INHERIT FALSE;

-- The API must never escalate to Worker.  A direct API grant to the Worker
-- login is removed as hygiene, but Worker intentionally reaches API through
-- wealthsail_worker -> wealthsail_api to run tenant-scoped report transactions.
REVOKE wealthsail_worker FROM wealthsail_api_login;
REVOKE wealthsail_api FROM wealthsail_worker_login;

-- Remove the old explicit shared-login grants. `postgres` remains a platform
-- administrator, but it must never appear in either runtime connection URL.
REVOKE wealthsail_api FROM postgres;
REVOKE wealthsail_worker FROM postgres;

DO $$
DECLARE
    api_can_api       BOOLEAN;
    api_can_worker    BOOLEAN;
    worker_can_api    BOOLEAN;
    worker_can_worker BOOLEAN;
BEGIN
    SELECT pg_has_role('wealthsail_api_login', 'wealthsail_api', 'SET'),
           pg_has_role('wealthsail_api_login', 'wealthsail_worker', 'SET'),
           pg_has_role('wealthsail_worker_login', 'wealthsail_api', 'SET'),
           pg_has_role('wealthsail_worker_login', 'wealthsail_worker', 'SET')
      INTO api_can_api, api_can_worker, worker_can_api, worker_can_worker;

    IF NOT api_can_api OR api_can_worker OR NOT worker_can_api OR NOT worker_can_worker THEN
        RAISE EXCEPTION
            'RUNTIME_ROLE_BOUNDARY_FAILED api->api=% api->worker=% worker->api=% worker->worker=%',
            api_can_api, api_can_worker, worker_can_api, worker_can_worker;
    END IF;
    RAISE NOTICE 'runtime role boundary OK; now inject two independent passwords';
END $$;
