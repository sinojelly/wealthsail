BEGIN;

-- 065: A web request may ask for a fund-screener refresh, but only the
-- Worker may contact AkShare and write the shared reference cache.  Reuse the
-- existing recoverable job-run lease model instead of process-local tasks.

ALTER TABLE workflow.job_runs
    DROP CONSTRAINT IF EXISTS job_runs_job_type_check;

ALTER TABLE workflow.job_runs
    ADD CONSTRAINT job_runs_job_type_check CHECK (
        job_type IS NULL OR job_type IN (
            'FUND_NAV_BACKFILL',
            'POSITION_PERIOD_METRICS_REBUILD',
            'FUND_NAV_RANGE_FETCH'
        )
    );

DROP INDEX IF EXISTS workflow.idx_job_runs_import_claimable;
CREATE INDEX idx_job_runs_data_claimable
    ON workflow.job_runs (job_type, status, available_at, next_retry_at, lease_until)
    WHERE job_type IN (
        'FUND_NAV_BACKFILL',
        'POSITION_PERIOD_METRICS_REBUILD',
        'FUND_NAV_RANGE_FETCH'
    )
      AND status IN ('PENDING', 'RUNNING', 'FAILED');

COMMENT ON INDEX workflow.idx_job_runs_data_claimable IS
    'Claim index for durable source-data tasks, including on-demand fund range refreshes.';

COMMIT;
