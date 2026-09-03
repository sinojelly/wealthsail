BEGIN;

-- Enable the FUND_1430 delivery path for tenants that never registered the
-- 14:30 job, and open the 14:30 preference only for members who already
-- receive another report session (so explicitly opted-out members keep
-- their choice).
--
-- workflow.jobs has no application-side writer; rows are provisioned by ops
-- or a migration like this one.  The cron_expr column is informational only
-- (the scheduler uses its built-in SessionSchedule table), but the column is
-- NOT NULL so a plausible value is stored.

INSERT INTO workflow.jobs (tenant_id, job_name, cron_expr, report_session, is_enabled)
SELECT tenants.id, 'fund_1430_report', '30 14 * * 1-5', 'FUND_1430', TRUE
  FROM identity.tenants tenants
 WHERE NOT EXISTS (
     SELECT 1
       FROM workflow.jobs jobs
      WHERE jobs.tenant_id = tenants.id
        AND jobs.report_session = 'FUND_1430'
        AND jobs.is_enabled = TRUE
 );

-- Fund 14:30 preferences: enable only for members that (a) already have at
-- least one report session enabled and (b) hold an ACTIVE wecom binding.
-- Members who disabled everything keep their explicit opt-out.
UPDATE identity.user_preferences preferences
   SET fund_1430_report_enabled = TRUE
 WHERE COALESCE(preferences.fund_1430_report_enabled, FALSE) IS DISTINCT FROM TRUE
   AND (
       COALESCE(preferences.pre_market_report_enabled, FALSE)
       OR COALESCE(preferences.intraday_report_enabled, FALSE)
       OR COALESCE(preferences.post_market_report_enabled, FALSE)
   )
   AND EXISTS (
       SELECT 1
         FROM identity.tenant_members membership
         JOIN identity.channel_bindings binding
           ON binding.tenant_id = membership.tenant_id
          AND binding.user_id = membership.user_id
          AND binding.channel_type = 'wecom'
          AND binding.status = 'ACTIVE'
        WHERE membership.user_id = preferences.user_id
          AND membership.status = 'ACTIVE'
   );

COMMIT;
