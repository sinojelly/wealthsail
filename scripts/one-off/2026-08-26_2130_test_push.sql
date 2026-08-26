-- ============================================================================
-- ONE-OFF ONLY — never load through scripts/migrate.py or production startup.
--
-- 目的：2026-08-26 21:30 给 so 租户(8c1b5af8)增加一次测试推送调度。
-- 这是一份保留审计上下文的一次性操作记录，不是可重放到所有环境的 schema
-- migration。执行前必须核对目标数据库和 tenant；验证完成后执行文件尾部回滚。
-- ============================================================================

-- 1) 放宽 reporting.reports 的会话 CHECK（临时加入 TEST_2130）
ALTER TABLE reporting.reports DROP CONSTRAINT IF EXISTS reports_report_session_check;
ALTER TABLE reporting.reports
    ADD CONSTRAINT reports_report_session_check
    CHECK (report_session IN ('PRE_MARKET', 'INTRADAY', 'FUND_1430', 'POST_MARKET', 'TEST_2130'));

-- 2) 放宽 workflow.jobs 的会话 CHECK（临时加入 TEST_2130）
ALTER TABLE workflow.jobs DROP CONSTRAINT IF EXISTS jobs_report_session_check;
ALTER TABLE workflow.jobs
    ADD CONSTRAINT jobs_report_session_check
    CHECK (report_session IN ('PRE_MARKET', 'INTRADAY', 'FUND_1430', 'POST_MARKET', 'TEST_2130'));

-- 3) 为 so 租户注册 21:30 测试 job（固定 UUID，幂等可重跑）
INSERT INTO workflow.jobs (id, tenant_id, job_name, cron_expr, report_session, is_enabled)
VALUES ('90000000-0000-0000-0000-000000002130',
        '8c1b5af8-772d-4abe-83cc-1eea1519a21a',
        'test_2130_push', '30 21 * * *', 'TEST_2130', TRUE)
ON CONFLICT (id) DO UPDATE
    SET job_name = EXCLUDED.job_name,
        cron_expr = EXCLUDED.cron_expr,
        report_session = EXCLUDED.report_session,
        is_enabled = EXCLUDED.is_enabled;

-- ----------------------------------------------------------------------------
-- 回滚：
--   ALTER TABLE reporting.reports DROP CONSTRAINT IF EXISTS reports_report_session_check;
--   ALTER TABLE reporting.reports ADD CONSTRAINT reports_report_session_check
--       CHECK (report_session IN ('PRE_MARKET','INTRADAY','FUND_1430','POST_MARKET'));
--   ALTER TABLE workflow.jobs DROP CONSTRAINT IF EXISTS jobs_report_session_check;
--   ALTER TABLE workflow.jobs ADD CONSTRAINT jobs_report_session_check
--       CHECK (report_session IN ('PRE_MARKET','INTRADAY','FUND_1430','POST_MARKET'));
--   DELETE FROM workflow.job_runs
--    WHERE job_id = '90000000-0000-0000-0000-000000002130';
--   DELETE FROM workflow.jobs
--    WHERE id = '90000000-0000-0000-0000-000000002130';
-- ----------------------------------------------------------------------------
