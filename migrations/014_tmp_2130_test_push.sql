-- ============================================================================
-- 014_tmp_2130_test_push.sql  [临时迁移，验证后需回滚]
-- 目的：今晚 21:30 给 so 租户(8c1b5af8) 增加一次测试推送调度。
-- 做法：临时允许 TEST_2130 会话名（两个 CHECK 约束），并为 so 租户注册该会话 job。
-- 回滚：执行 scripts/revert_2130_test_push.sql（或见文件尾部注释），并还原
--       backend/jobs/scheduler.py 与 backend/reports/service.py 中的临时条目。
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
-- 回滚脚本（验证完成后执行）：
--   ALTER TABLE reporting.reports DROP CONSTRAINT IF EXISTS reports_report_session_check;
--   ALTER TABLE reporting.reports ADD CONSTRAINT reports_report_session_check
--       CHECK (report_session IN ('PRE_MARKET','INTRADAY','FUND_1430','POST_MARKET'));
--   ALTER TABLE workflow.jobs DROP CONSTRAINT IF EXISTS jobs_report_session_check;
--   ALTER TABLE workflow.jobs ADD CONSTRAINT jobs_report_session_check
--       CHECK (report_session IN ('PRE_MARKET','INTRADAY','FUND_1430','POST_MARKET'));
--   DELETE FROM workflow.jobs WHERE id = '90000000-0000-0000-0000-000000002130';
--   DELETE FROM workflow.job_runs WHERE job_id = '90000000-0000-0000-0000-000000002130';
-- ----------------------------------------------------------------------------
