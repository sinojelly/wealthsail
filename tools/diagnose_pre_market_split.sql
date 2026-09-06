-- =====================================================================
-- 盘前拆分（08:30 宏观 / 08:35 个性化）第二次推送失败 — 只读诊断
-- 用法（migration owner DSN，只读不写库）：
--   psql "$MIGRATION_DATABASE_URL" -X -P pager=off -f tools/diagnose_pre_market_split.sql
-- 也可以把下面任意一条 SELECT 单独贴到 Supabase SQL Editor 执行。
-- =====================================================================

\echo '== 1) 最近一个交易日的盘前 job_runs（宏观+个性化两个 phase 的状态）=='
SELECT job_key, status, error_class, attempt_count,
       to_char(started_at  AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI:SS') AS started,
       to_char(finished_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI:SS') AS finished
  FROM workflow.job_runs
 WHERE job_key LIKE '%PRE_MARKET%'
   AND started_at >= now() - interval '7 days'
 ORDER BY started_at DESC
 LIMIT 20;

\echo '== 2) 最近交易日的盘前报告（两个 variant 是否都生成了）=='
SELECT report_variant,
       left(audience_user_id::text, 8) AS audience,
       status, version, quality_status,
       to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') AS created
  FROM reporting.reports
 WHERE report_session = 'PRE_MARKET'
   AND created_at >= now() - interval '7 days'
 ORDER BY created_at DESC
 LIMIT 20;

\echo '== 3) 这些报告的 outbox 投递行（PENDING/SENT/DEAD + 错误）=='
SELECT r.report_variant,
       left(o.id::text, 8) AS outbox_id,
       o.status, o.attempt_count, o.last_error_class,
       o.external_message_id IS NOT NULL AS has_wecom_receipt,
       to_char(o.sent_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI:SS') AS sent_at
  FROM reporting.reports r
  JOIN workflow.outbox o ON o.aggregate_id = r.id AND o.topic = 'REPORT_PUBLISHED'
 WHERE r.report_session = 'PRE_MARKET'
   AND r.created_at >= now() - interval '7 days'
 ORDER BY r.created_at DESC, o.created_at
 LIMIT 20;

\echo '== 4) 活跃成员的关注列表（08:35 无持仓且有自选才不会被 SKIP）=='
SELECT left(m.user_id::text, 8) AS member,
       m.status AS membership,
       count(w.symbol) AS watchlist_rows,
       count(*) FILTER (WHERE alias.instrument_id IS NOT NULL) AS resolvable_rows
  FROM identity.tenant_members m
  LEFT JOIN identity.watchlist w ON w.user_id = m.user_id
  LEFT JOIN reference.instrument_aliases alias
         ON alias.alias_type = 'TICKER'
        AND upper(alias.alias_value) = w.symbol
        AND alias.effective_from <= CURRENT_DATE
        AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
 WHERE m.status = 'ACTIVE'
 GROUP BY m.user_id, m.status
 ORDER BY m.created_at;

\echo '== 5) 活跃成员的企微绑定与盘前推送开关（投递门）=='
SELECT left(m.user_id::text, 8) AS member,
       count(b.id) FILTER (WHERE b.channel_type = 'wecom' AND b.status = 'ACTIVE') AS active_wecom_bindings,
       bool_or(preference.pre_market_report_enabled) AS pre_market_enabled
  FROM identity.tenant_members m
  LEFT JOIN identity.channel_bindings b ON b.tenant_id = m.tenant_id AND b.user_id = m.user_id
  LEFT JOIN identity.user_preferences preference ON preference.user_id = m.user_id
 WHERE m.status = 'ACTIVE'
 GROUP BY m.user_id
 ORDER BY m.created_at;

\echo '== 6) 盘前定时作业配置（应为 enabled）=='
SELECT job_name, report_session, is_enabled,
       to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD') AS created
  FROM workflow.jobs
 WHERE report_session = 'PRE_MARKET';
