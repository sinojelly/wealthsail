-- ==============================================================================
-- WealthSail Migration 009: Domain API Write & Operations-Read Policies
-- Version: v2.2.0 (local MVP bridge)
-- Description: The v2.2 Domain API executes business transactions under the
--              `authenticated` role (SET LOCAL ROLE authenticated) so that
--              tenant RLS is the isolation boundary even for application code.
--              Migrations 005/007/008 intentionally grant only SELECT to
--              `authenticated`; trusted application writes (reports, model
--              runs, outbox, audit, binding challenges, job runs) therefore
--              need explicit, tenant-scoped INSERT/UPDATE policies plus the
--              corresponding table/column privileges.
--
--              Every write policy is scoped WITH CHECK
--              (identity.is_tenant_member(tenant_id)): the Domain API can only
--              create or mutate rows inside a tenant the acting user belongs
--              to. In Supabase production this file is a no-op for the
--              browser-facing role and the Domain API would run under a
--              dedicated service role instead.
-- ==============================================================================

-- 1. Tenant-scoped INSERT policies for Domain API writes.
DO $$
DECLARE
    t TEXT;
    insert_tables TEXT[] := ARRAY[
        'research.model_runs',
        'reporting.reports',
        'reporting.report_items',
        'workflow.jobs',
        'workflow.job_runs',
        'workflow.outbox',
        'audit.audit_log',
        'identity.binding_challenges',
        'identity.channel_bindings',
        -- Derived projections are recomputed by the market pipeline inside
        -- the per-tenant RLS transaction (worker role); see migrations 010.
        'ledger.position_snapshots',
        'ledger.account_nav_snapshots'
    ];
BEGIN
    FOREACH t IN ARRAY insert_tables LOOP
        EXECUTE format('DROP POLICY IF EXISTS domain_api_insert ON %s', t);
        EXECUTE format(
            'CREATE POLICY domain_api_insert ON %s FOR INSERT TO authenticated
             WITH CHECK (identity.is_tenant_member(tenant_id))',
            t
        );
    END LOOP;
END $$;

-- 2. Tenant-scoped UPDATE policies for state-machine tables the Domain API
--    mutates. Database triggers (007/008) remain the final integrity boundary.
DO $$
DECLARE
    t TEXT;
    update_tables TEXT[] := ARRAY[
        'workflow.job_runs',
        'workflow.outbox',
        'identity.binding_challenges',
        'identity.channel_bindings',
        -- Projection upserts (ON CONFLICT DO UPDATE) from the market pipeline.
        'ledger.position_snapshots',
        'ledger.account_nav_snapshots'
    ];
BEGIN
    FOREACH t IN ARRAY update_tables LOOP
        EXECUTE format('DROP POLICY IF EXISTS domain_api_update ON %s', t);
        EXECUTE format(
            'CREATE POLICY domain_api_update ON %s FOR UPDATE TO authenticated
             USING (identity.is_tenant_member(tenant_id))
             WITH CHECK (identity.is_tenant_member(tenant_id))',
            t
        );
    END LOOP;
END $$;

-- 3. Column-level INSERT/UPDATE privileges (least privilege; no blanket grants).
GRANT INSERT ON
    research.model_runs,
    reporting.reports,
    reporting.report_items,
    workflow.jobs,
    workflow.job_runs,
    workflow.outbox,
    audit.audit_log,
    identity.binding_challenges,
    identity.channel_bindings,
    ledger.position_snapshots,
    ledger.account_nav_snapshots
TO authenticated;

-- Only the state columns a worker/runner may transition.
GRANT UPDATE (status, finished_at, error_class) ON workflow.job_runs TO authenticated;
GRANT UPDATE (status, attempt_count, last_error_class, locked_at, locked_by,
              external_message_id, sent_at) ON workflow.outbox TO authenticated;
GRANT UPDATE (status, consumed_at, resolved_at, binding_id, attempt_count, version)
    ON identity.binding_challenges TO authenticated;
GRANT UPDATE (status, bound_at, revoked_at, last_used_at, version)
    ON identity.channel_bindings TO authenticated;
-- Projection columns recomputed by the market pipeline (quantities and cost
-- basis are immutable; only market-derived values may change).
GRANT UPDATE (market_price, market_value, unrealized_pnl, data_quality_status)
    ON ledger.position_snapshots TO authenticated;
GRANT UPDATE (cash_balance, positions_market_value, total_nav, daily_pnl,
              cumulative_pnl, data_quality_status)
    ON ledger.account_nav_snapshots TO authenticated;

-- 4. Operations pages are served by the sanitizing Domain API: restore
--    tenant-scoped SELECT (OWNER/ADMIN only, matching migration 008) on the
--    operational tables, but keep sensitive payload/detail columns hidden.
GRANT SELECT (
    id, tenant_id, job_name, cron_expr, report_session, is_enabled, created_at
) ON workflow.jobs TO authenticated;
GRANT SELECT (
    id, tenant_id, job_id, job_key, started_at, finished_at, status, error_class
) ON workflow.job_runs TO authenticated;
GRANT SELECT (
    id, tenant_id, topic, aggregate_type, aggregate_id, schema_version,
    available_at, status, attempt_count, idempotency_key, sent_at, created_at
) ON workflow.outbox TO authenticated;
GRANT SELECT (
    id, tenant_id, actor_type, actor_id, action, resource_type, resource_id,
    correlation_id, created_at
) ON audit.audit_log TO authenticated;

-- 5. Non-sensitive binding challenge metadata for the settings page.
GRANT SELECT (
    id, tenant_id, user_id, channel_type, expires_at, attempt_count,
    max_attempts, status, consumed_at, resolved_at, binding_id, version, created_at
) ON identity.binding_challenges TO authenticated;

-- 6. Migration 005 granted schema USAGE to `authenticated` but omitted the
--    reference/market schemas, which tenant reads JOIN (instruments, aliases,
--    market observations). Restore them here.
GRANT USAGE ON SCHEMA reference, market TO authenticated;
GRANT SELECT ON
    reference.instruments,
    reference.instrument_aliases,
    reference.trading_calendars,
    reference.data_sources,
    market.source_snapshots,
    market.market_observations
TO authenticated;
