BEGIN;

-- 066: The fund-screener's on-demand fetch flow made the API a first-time
-- writer of workflow.job_runs, exposing two long-missing grants.  The API
-- role must be able to (a) read back the row it just inserted via
-- INSERT..RETURNING, which requires table-level SELECT, and (b) requeue a
-- terminal request (completed, or failed with retries exhausted) by
-- resetting its scheduling columns.  The Worker's grants (013) already cover
-- claiming and running; only the API side was incomplete.

GRANT SELECT ON workflow.job_runs TO wealthsail_api;

GRANT UPDATE (available_at, started_at, next_retry_at, attempt_count,
              lease_token, lease_owner, lease_until, heartbeat_at)
    ON workflow.job_runs TO wealthsail_api;

COMMENT ON TABLE workflow.job_runs IS
    'Durable job runs; 066 grants the API role SELECT plus the scheduling '
    'columns it needs to requeue terminal fund-screener fetch requests.';

COMMIT;
