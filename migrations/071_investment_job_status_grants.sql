BEGIN;
-- Narrow the legacy 066 table-wide read grant to the columns used by durable
-- task enqueue/requeue and status APIs; tenant row-level policy is unchanged.
REVOKE SELECT ON workflow.job_runs FROM wealthsail_api;
GRANT SELECT(id,tenant_id,job_key,status,job_type,payload,available_at,
 started_at,finished_at,error_class,next_retry_at,attempt_count,lease_token,
 lease_owner,lease_until,heartbeat_at) ON workflow.job_runs TO wealthsail_api;
COMMIT;
