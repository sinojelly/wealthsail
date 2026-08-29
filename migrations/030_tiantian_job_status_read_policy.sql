-- =============================================================================
-- WealthSail Migration 030: tenant-scoped Tiantian job-status reads
--
-- The Tiantian batch-detail endpoint correlates a confirmed import with the
-- latest FUND_NAV_BACKFILL or POSITION_PERIOD_METRICS_REBUILD task. Migration
-- 021 made workflow.job_runs worker-owned but did not grant the API the SELECT
-- capability required by that read path, causing a 500 after the query reached
-- PostgreSQL.
--
-- Keep the repair least-privileged:
--   * RLS exposes only rows for the active tenant membership;
--   * the API receives only the six columns used by the correlation query;
--   * lease ownership, retry diagnostics and error text remain worker-only;
--   * browser roles receive no new workflow access.
-- =============================================================================

DROP POLICY IF EXISTS job_runs_api_select ON workflow.job_runs;
CREATE POLICY job_runs_api_select
ON workflow.job_runs
FOR SELECT TO wealthsail_api
USING (identity.is_tenant_member(tenant_id));

GRANT SELECT (id, tenant_id, status, job_type, payload, available_at)
ON workflow.job_runs TO wealthsail_api;

COMMENT ON POLICY job_runs_api_select ON workflow.job_runs IS
    'API may correlate import data-task status only within an active tenant membership.';
