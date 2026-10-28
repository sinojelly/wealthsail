-- Read-only investigation; never dump details, credentials or account identity.
-- Run using an existing authorized operator connection (RLS is unchanged):
--   psql ... -X -v task_prefix=270c10fc -f tools/diagnose_ai_usage_snapshots.sql
-- At least 8 hexadecimal task ID characters are required. No model RPC is made.
\set ON_ERROR_STOP on
\if :{?task_prefix}
\else
\echo 'Required: -v task_prefix=<at least 8 hexadecimal task ID characters>'
\quit
\endif

BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';

-- Boundary presence, identity/observation comparisons and sanitized diagnostics.
WITH calls AS (
    SELECT c.*, t.started_at AS task_started_at
      FROM research.ai_usage_calls c
      JOIN research.ai_usage_tasks t ON t.id = c.task_id
     WHERE :'task_prefix' ~ '^[0-9a-fA-F]{8,32}$'
       AND replace(t.id::text, '-', '') LIKE lower(:'task_prefix') || '%'
     ORDER BY c.started_at DESC
     LIMIT 100
), samples AS (
    SELECT c.*, stage,
           CASE WHEN jsonb_typeof(details -> stage) = 'object'
                THEN details -> stage ELSE '{}'::jsonb END AS sample
      FROM calls c CROSS JOIN (VALUES ('before'), ('after')) AS boundary(stage)
)
SELECT left(task_id::text, 8) AS task_id, left(id::text, 8) AS call_id,
       to_char(task_started_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI:SS') AS task_start_beijing,
       status AS inference_status, concurrent, stage,
       sample <> '{}'::jsonb AS capture_present,
       CASE WHEN sample ->> 'status' IN ('READY','STALE','UNAVAILABLE','UNAUTHENTICATED')
            THEN sample ->> 'status'
            WHEN sample = '{}'::jsonb THEN 'MISSING' ELSE 'LEGACY' END AS capture_status,
       CASE WHEN sample #>> '{diagnostic,reason}' IN ('TIMEOUT','UNAVAILABLE','NOT_FRESH','ERROR','CANCELLED')
            THEN sample #>> '{diagnostic,reason}' END AS capture_failure,
       CASE WHEN sample #>> '{diagnostic,elapsedMs}' ~ '^[0-9]{1,10}$'
            THEN sample #>> '{diagnostic,elapsedMs}' END AS elapsed_ms,
       CASE WHEN sample ->> 'observedAt' ~ '^\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+$'
            THEN sample ->> 'observedAt' END AS observed_at_iso,
       coalesce(length(sample ->> 'accountFingerprint') > 0, false) AS has_identity,
       coalesce(length(sample ->> 'observationId') > 0, false) AS has_observation_id,
       coalesce(length(sample ->> 'authGeneration') > 0, false) AS has_auth_generation,
       nullif(details #>> '{before,accountFingerprint}', '') = nullif(details #>> '{after,accountFingerprint}', '') AS same_identity,
       nullif(details #>> '{before,observationId}', '') = nullif(details #>> '{after,observationId}', '') AS same_observation,
       nullif(details #>> '{before,authGeneration}', '') = nullif(details #>> '{after,authGeneration}', '') AS same_auth_generation
  FROM samples
 ORDER BY task_started_at, id, stage DESC;

-- Quota windows are independent of credits. Missing credits do not prevent a
-- valid 5-hour percentage-point estimate. Times are supplied as epoch seconds.
WITH calls AS (
    SELECT c.* FROM research.ai_usage_calls c
      JOIN research.ai_usage_tasks t ON t.id = c.task_id
     WHERE :'task_prefix' ~ '^[0-9a-fA-F]{8,32}$'
       AND replace(t.id::text, '-', '') LIKE lower(:'task_prefix') || '%'
     ORDER BY c.started_at DESC LIMIT 100
), samples AS (
    SELECT c.id, c.started_at, stage, details -> stage AS sample
      FROM calls c CROSS JOIN (VALUES ('before'), ('after')) AS boundary(stage)
)
SELECT left(id::text, 8) AS call_id, stage,
       CASE WHEN w ->> 'key' ~ '^[A-Za-z0-9:_-]{1,140}$'
            THEN w ->> 'key' END AS window_key,
       CASE WHEN w ->> 'usedPercent' ~ '^[0-9]+(\.[0-9]+)?$'
            THEN w ->> 'usedPercent' END AS used_percent,
       CASE WHEN w ->> 'windowMinutes' ~ '^[0-9]{1,8}$'
            THEN w ->> 'windowMinutes' END AS window_minutes,
       CASE WHEN w ->> 'resetsAt' ~ '^[0-9]{1,19}$'
            THEN w ->> 'resetsAt' END AS resets_at_epoch,
       CASE WHEN sample #>> '{credits,balance}' ~ '^[0-9]+(\.[0-9]+)?$'
            THEN sample #>> '{credits,balance}' END AS credits_balance,
       CASE WHEN sample #>> '{credits,unlimited}' IN ('true','false')
            THEN sample #>> '{credits,unlimited}' END AS credits_unlimited
  FROM samples
  LEFT JOIN LATERAL jsonb_array_elements(
      CASE WHEN jsonb_typeof(sample -> 'windows') = 'array'
           THEN sample -> 'windows' ELSE '[]'::jsonb END
  ) AS window_row(w) ON true
 ORDER BY started_at, id, stage DESC, window_key;

ROLLBACK;
