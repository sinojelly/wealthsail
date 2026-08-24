-- ==============================================================================
-- WealthSail Migration 010: Local Dev Seed for Web Workbench Demo
-- Version: v2.2.0 (dev-only; do NOT ship to production tenants)
-- Description: Backfills deterministic demo data for pilot tenant 1 (张明) so
--              the Web workbench pages render real API data:
--                * 30 trading days of NAV history (nav-trend chart)
--                * CONFIRMED + DRAFT investment policies (version audit)
--                * alert rules + alert events (risk rules page)
--                * workflow jobs + job runs (operations page)
--                * model runs, four-session reports with items + a v2 revision
--                * outbox rows in PENDING/SENT/DEAD states + audit events
--              All inserts are idempotent (ON CONFLICT DO NOTHING).
-- ==============================================================================

-- 1. NAV history: 30 trading days ending 2026-08-24, deterministic walk
--    480,000.00 -> 506,000.00 (principal 500,000; matches migration 006).
DO $$
DECLARE
    d DATE := '2026-07-01';
    idx INT := 0;
    nav NUMERIC(28, 8);
    prev NUMERIC(28, 8);
BEGIN
    WHILE idx < 40 LOOP
        IF EXTRACT(ISODOW FROM d) < 6 THEN
            idx := idx + 1;
            -- Insert trading days 10..39 (30 rows): 2026-07-14 .. 2026-08-24.
            -- 2026-08-24 already exists from migration 006 (the anchor row),
            -- so the total history is exactly 30 trading days.
            IF idx >= 10 AND idx < 40 THEN
                nav := 480000.00 + (idx - 11) * (26000.00 / 29);
                prev := nav - (26000.00 / 29);
                INSERT INTO ledger.account_nav_snapshots
                    (tenant_id, account_id, as_of, cash_balance, positions_market_value,
                     total_nav, daily_pnl, cumulative_pnl, data_quality_status)
                VALUES
                    ('00000000-0000-0000-0000-000000000001',
                     '30000000-0000-0000-0000-000000000001',
                     d, 80000.00, nav - 80000.00, nav,
                     ROUND(nav - prev, 2), ROUND(nav - 500000.00, 2), 'FRESH')
                ON CONFLICT (tenant_id, account_id, as_of) DO NOTHING;
            END IF;
        END IF;
        d := d + 1;
    END LOOP;
END $$;

-- 2. Investment policies: current CONFIRMED v3 (legacy) + DRAFT v4 preview.
INSERT INTO risk.investment_policies
    (id, tenant_id, account_id, max_drawdown_limit, single_position_limit,
     equity_target_pct, bond_target_pct, gold_target_pct, cash_target_pct,
     rebalance_tolerance_pct, policy_status, effective_from, effective_to,
     version, confirmation_provenance)
VALUES
    ('76000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', NULL,
     15.00, 30.00, 60.00, 20.00, 10.00, 10.00, 2.00,
     'CONFIRMED', DATE '2026-07-15', NULL, 3, 'LEGACY_MIGRATED'),
    ('76000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', NULL,
     12.00, 25.00, 55.00, 25.00, 10.00, 10.00, 2.00,
     'DRAFT', DATE '2026-08-24', NULL, 4, NULL)
ON CONFLICT (id) DO NOTHING;

-- 3. Alert rules (触点风控) for tenant 1.
INSERT INTO risk.alert_rules
    (id, tenant_id, account_id, instrument_id, thesis_id, rule_type,
     rule_params, silence_window_minutes, status)
VALUES
    ('77000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001',
     '30000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001',
     '50000000-0000-0000-0000-000000000001', 'PRICE_TOUCH_STOP_LOSS',
     '{"stop_loss_pct": 8.0, "observe_line": 43.50}'::jsonb, 60, 'ACTIVE'),
    ('77000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001',
     '30000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000003',
     NULL, 'ESTIMATED_NAV_SWING',
     '{"swing_pct": 1.5, "coverage_threshold_pct": 70.0}'::jsonb, 60, 'ACTIVE'),
    ('77000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001',
     NULL, NULL, NULL, 'MACRO_CYCLE_SHIFT',
     '{"indicator": "PMI", "threshold": 50}'::jsonb, 1440, 'ACTIVE')
ON CONFLICT (id) DO NOTHING;

-- 4. Workflow jobs (四时段) + job runs history for operations page.
INSERT INTO workflow.jobs
    (id, tenant_id, job_name, cron_expr, report_session, is_enabled)
VALUES
    ('70000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001',
     'pre_market_brief', '0 30 8 * * 1-5', 'PRE_MARKET', TRUE),
    ('70000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001',
     'intraday_tracking', '*/5 9-14 * * 1-5', 'INTRADAY', TRUE),
    ('70000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001',
     'fund_1430_estimate', '0 30 14 * * 1-5', 'FUND_1430', TRUE),
    ('70000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001',
     'post_market_review', '0 30 15 * * 1-5', 'POST_MARKET', TRUE)
ON CONFLICT (id) DO NOTHING;

INSERT INTO workflow.job_runs
    (id, tenant_id, job_id, job_key, started_at, finished_at, status, error_class)
VALUES
    ('71000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001',
     '70000000-0000-0000-0000-000000000001', 'pre_market_brief:2026-08-24:083000',
     TIMESTAMPTZ '2026-08-24 08:30:00+08', TIMESTAMPTZ '2026-08-24 08:30:42+08', 'COMPLETED', NULL),
    ('71000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001',
     '70000000-0000-0000-0000-000000000002', 'intraday_tracking:2026-08-24:112800',
     TIMESTAMPTZ '2026-08-24 11:28:00+08', TIMESTAMPTZ '2026-08-24 11:28:31+08', 'COMPLETED', NULL),
    ('71000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001',
     '70000000-0000-0000-0000-000000000003', 'fund_1430_estimate:2026-08-24:143000',
     TIMESTAMPTZ '2026-08-24 14:30:00+08', TIMESTAMPTZ '2026-08-24 14:31:05+08', 'COMPLETED', NULL),
    ('71000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001',
     '70000000-0000-0000-0000-000000000004', 'post_market_review:2026-08-24:153000',
     TIMESTAMPTZ '2026-08-24 15:30:00+08', NULL, 'FAILED', 'ModelTimeoutError'),
    ('71000000-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001',
     '70000000-0000-0000-0000-000000000004', 'post_market_review:2026-08-24:153530',
     TIMESTAMPTZ '2026-08-24 15:35:30+08', TIMESTAMPTZ '2026-08-24 15:36:58+08', 'COMPLETED', NULL),
    ('71000000-0000-0000-0000-000000000006', '00000000-0000-0000-0000-000000000001',
     '70000000-0000-0000-0000-000000000001', 'pre_market_brief:2026-08-21:083000',
     TIMESTAMPTZ '2026-08-21 08:30:00+08', TIMESTAMPTZ '2026-08-21 08:30:39+08', 'COMPLETED', NULL),
    ('71000000-0000-0000-0000-000000000007', '00000000-0000-0000-0000-000000000001',
     '70000000-0000-0000-0000-000000000002', 'intraday_tracking:2026-08-21:101500',
     TIMESTAMPTZ '2026-08-21 10:15:00+08', TIMESTAMPTZ '2026-08-21 10:15:22+08', 'ABSTAINED', NULL)
ON CONFLICT (id) DO NOTHING;

-- 5. Model runs (DeepSeek) referenced by the seeded reports.
INSERT INTO research.model_runs
    (id, tenant_id, provider, model, prompt_template_version, skill_version,
     input_snapshot_hashes, tool_calls, structured_output_hash, token_usage,
     cost_cny, started_at, finished_at, status, correlation_id)
VALUES
    ('60000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001',
     'deepseek', 'deepseek-chat', 'report.pre_market.v1', 'weighthsail.report.v1',
     '{"positions": "sha256:seed-1", "market": "sha256:seed-1"}'::jsonb,
     '[]'::jsonb, 'sha256:seed-report-1', '{"prompt_tokens": 1240, "completion_tokens": 860}'::jsonb,
     0.0315, TIMESTAMPTZ '2026-08-24 08:29:55+08', TIMESTAMPTZ '2026-08-24 08:30:40+08',
     'COMPLETED', 'corr-seed-pre-1'),
    ('60000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001',
     'deepseek', 'deepseek-chat', 'report.post_market.v1', 'weighthsail.report.v1',
     '{"positions": "sha256:seed-2", "decisions": "sha256:seed-2"}'::jsonb,
     '[]'::jsonb, 'sha256:seed-report-2', '{"prompt_tokens": 980, "completion_tokens": 720}'::jsonb,
     0.0262, TIMESTAMPTZ '2026-08-24 15:35:40+08', TIMESTAMPTZ '2026-08-24 15:36:55+08',
     'COMPLETED', 'corr-seed-post-1')
ON CONFLICT (id) DO NOTHING;

-- 6. Four-session reports for 2026-08-24 + a PRE_MARKET v2 revision
--    (supersedes v1) to exercise the version-audit view.
INSERT INTO reporting.reports
    (id, tenant_id, report_session, trade_date, version, status, as_of,
     quality_status, summary, model_run_id, job_run_id, policy_check_status,
     valid_until, supersedes_report_id)
VALUES
    ('72000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001',
     'PRE_MARKET', DATE '2026-08-24', 1, 'PUBLISHED', TIMESTAMPTZ '2026-08-24 08:30:00+08',
     'FRESH', '隔夜美股三大指数收涨，A50 期货小幅走高；今日关注 09:30 行业数据与白酒板块资金流向。持仓茅台若跌破 1350 元观察位则触发出局条件。',
     '60000000-0000-0000-0000-000000000001', '71000000-0000-0000-0000-000000000001',
     'PASSED', TIMESTAMPTZ '2026-08-24 10:30:00+08', NULL),
    ('72000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001',
     'PRE_MARKET', DATE '2026-08-24', 2, 'PUBLISHED', TIMESTAMPTZ '2026-08-24 08:45:00+08',
     'FRESH', '盘前简报修订版：补充茅台分红除权日历与渠道批价快讯，出局条件维持不变。',
     '60000000-0000-0000-0000-000000000001', '71000000-0000-0000-0000-000000000001',
     'PASSED', TIMESTAMPTZ '2026-08-24 10:45:00+08', '72000000-0000-0000-0000-000000000001'),
    ('72000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001',
     'INTRADAY', DATE '2026-08-24', 1, 'PUBLISHED', TIMESTAMPTZ '2026-08-24 11:28:00+08',
     'FRESH', '盘中跟踪：持仓标的无异动，沪深300 指数 +0.39%；未触发任何风控触点。',
     NULL, '71000000-0000-0000-0000-000000000002',
     'PASSED', TIMESTAMPTZ '2026-08-24 15:00:00+08', NULL),
    ('72000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001',
     'FUND_1430', DATE '2026-08-24', 1, 'PUBLISHED', TIMESTAMPTZ '2026-08-24 14:30:00+08',
     'ESTIMATED', '场外基金估值：易方达优质精选混合估算净值 1.7326（+0.48%），持仓覆盖率 72.4%；14:30 后申购按次日净值确认。',
     NULL, '71000000-0000-0000-0000-000000000003',
     'PASSED', TIMESTAMPTZ '2026-08-24 16:30:00+08', NULL),
    ('72000000-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001',
     'POST_MARKET', DATE '2026-08-24', 1, 'PUBLISHED', TIMESTAMPTZ '2026-08-24 16:35:00+08',
     'FRESH', '盘后复盘：盘前计划与当日动作核对完成，无成交记录；茅台当日 +1.43%，组合净值 506,000 元，当日收益 +6,000 元。',
     '60000000-0000-0000-0000-000000000002', '71000000-0000-0000-0000-000000000005',
     'PASSED', TIMESTAMPTZ '2026-08-24 23:59:59+08', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO reporting.report_items
    (id, report_id, tenant_id, item_type, subject_id, severity, title, body,
     action_plan, data_quality_status, sort_order)
VALUES
    ('73000000-0000-0000-0000-000000000001', '72000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001', 'FACT', '20000000-0000-0000-0000-000000000001',
     'INFO', '隔夜外盘', '标普500 +0.42%，纳斯达克 +0.61%，半导体板块相对活跃。',
     NULL, 'FRESH', 1),
    ('73000000-0000-0000-0000-000000000002', '72000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001', 'RISK', '20000000-0000-0000-0000-000000000001',
     'WARNING', '持仓出局条件', '日线收盘破位 1350 元且 3 日内无法收回则触发减仓预案。',
     '{"if": "收盘价 < 1350", "then": "次日开盘观察，跌破 1330 减仓 1/3", "else": "维持持仓"}'::jsonb,
     'FRESH', 2),
    ('73000000-0000-0000-0000-000000000003', '72000000-0000-0000-0000-000000000003',
     '00000000-0000-0000-0000-000000000001', 'FACT', NULL,
     'INFO', '盘中概况', '沪深300 +0.39%，白酒板块资金净流入；持仓标的无触发告警。',
     NULL, 'FRESH', 1),
    ('73000000-0000-0000-0000-000000000004', '72000000-0000-0000-0000-000000000004',
     '00000000-0000-0000-0000-000000000001', 'FUND_ESTIMATE', '20000000-0000-0000-0000-000000000003',
     'INFO', '易方达优质精选混合估值', '估算净值 1.7326，较上一净值 +0.48%；估值基于最近披露持仓，覆盖率 72.4%。',
     NULL, 'ESTIMATED', 1),
    ('73000000-0000-0000-0000-000000000005', '72000000-0000-0000-0000-000000000005',
     '00000000-0000-0000-0000-000000000001', 'ACTION_PLAN', NULL,
     'INFO', '明日关注', '跟踪茅台批价与北向资金；若基金估值覆盖率低于 70% 标记黄底警告。',
     NULL, 'FRESH', 1)
ON CONFLICT (id) DO NOTHING;

-- 7. Outbox rows in three lifecycle states for the message-delivery panel.
INSERT INTO workflow.outbox
    (id, tenant_id, topic, aggregate_type, aggregate_id, payload, schema_version,
     idempotency_key, available_at, status, attempt_count, last_error_class,
     external_message_id, sent_at)
VALUES
    ('74000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001',
     'REPORT_PUBLISHED', 'reporting.reports', '72000000-0000-0000-0000-000000000003',
     '{"report_id": "72000000-0000-0000-0000-000000000003", "session": "INTRADAY"}'::jsonb,
     1, 'seed-intraday-20260824-1', TIMESTAMPTZ '2026-08-24 11:28:31+08',
     'PENDING', 0, NULL, NULL, NULL),
    ('74000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001',
     'REPORT_PUBLISHED', 'reporting.reports', '72000000-0000-0000-0000-000000000001',
     '{"report_id": "72000000-0000-0000-0000-000000000001", "session": "PRE_MARKET"}'::jsonb,
     1, 'seed-pre-20260824-1', TIMESTAMPTZ '2026-08-24 08:30:42+08',
     'SENT', 1, NULL, 'wecom_msg_seed_0001', TIMESTAMPTZ '2026-08-24 08:30:45+08'),
    ('74000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001',
     'ALERT_EVENT', 'risk.alert_events', '78000000-0000-0000-0000-000000000002',
     '{"rule_id": "77000000-0000-0000-0000-000000000002"}'::jsonb,
     1, 'seed-alert-20260824-1', TIMESTAMPTZ '2026-08-23 09:10:00+08',
     'DEAD', 5, 'WeComApiRateLimitError', NULL, NULL)
ON CONFLICT (id) DO NOTHING;

-- 8. Alert events + audit log.
INSERT INTO risk.alert_events
    (id, tenant_id, rule_id, fingerprint, dedupe_key, triggered_at, status, outbox_id)
VALUES
    ('78000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001',
     '77000000-0000-0000-0000-000000000001', 'fp-600519-20260824-1047', '600519:2026-08-24:10:47',
     TIMESTAMPTZ '2026-08-24 10:47:00+08', 'OPEN', NULL),
    ('78000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001',
     '77000000-0000-0000-0000-000000000002', 'fp-110011-20260823-0910', '110011:2026-08-23:09:10',
     TIMESTAMPTZ '2026-08-23 09:10:00+08', 'DELIVERED', '74000000-0000-0000-0000-000000000003')
ON CONFLICT (id) DO NOTHING;

INSERT INTO audit.audit_log
    (id, tenant_id, actor_type, actor_id, action, resource_type, resource_id,
     correlation_id, details, created_at)
VALUES
    ('75000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001',
     'SYSTEM_WORKER', 'worker:pre_market', 'REPORT_GENERATED', 'reporting.reports',
     '72000000-0000-0000-0000-000000000001', 'corr-seed-pre-1',
     '{"session": "PRE_MARKET", "version": 1}'::jsonb, TIMESTAMPTZ '2026-08-24 08:30:42+08'),
    ('75000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001',
     'USER', '00000000-0000-0000-0000-000000000001', 'SESSION_LOGIN', 'identity.session',
     '00000000-0000-0000-0000-000000000001', NULL,
     '{"method": "password", "device": "web-workbench"}'::jsonb, TIMESTAMPTZ '2026-08-24 09:02:11+08'),
    ('75000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001',
     'SYSTEM_WORKER', 'worker:outbox', 'MESSAGE_DELIVERED', 'workflow.outbox',
     '74000000-0000-0000-0000-000000000002', NULL,
     '{"topic": "REPORT_PUBLISHED", "external_message_id": "wecom_msg_seed_0001"}'::jsonb,
     TIMESTAMPTZ '2026-08-24 08:30:45+08')
ON CONFLICT (id) DO NOTHING;
