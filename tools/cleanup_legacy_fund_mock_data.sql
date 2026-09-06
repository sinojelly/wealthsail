-- 清理 053/054、已撤销 055 或旧手工脚本可能写入的基金模拟/近似业务事实。
--
-- 默认拒绝执行。正式清理必须同时传入：
--   -v apply_cleanup=true
--   -v cleanup_confirmation=DELETE_LEGACY_FUND_MOCK_DATA
--
-- 示例：
-- psql "$MIGRATION_DATABASE_URL" -X -v ON_ERROR_STOP=1 \
--   -v apply_cleanup=true \
--   -v cleanup_confirmation=DELETE_LEGACY_FUND_MOCK_DATA \
--   -f tools/cleanup_legacy_fund_mock_data.sql
--
-- 本脚本刻意保留：基金/股票 instruments、TICKER aliases、真实披露、NAV、
-- ESTIMATED_NAV、来源快照、历史报告和 migration ledger。

\set ON_ERROR_STOP on
\pset pager off

\if :{?apply_cleanup}
\else
  \set apply_cleanup false
\endif

\if :{?cleanup_confirmation}
  SELECT :'cleanup_confirmation' = 'DELETE_LEGACY_FUND_MOCK_DATA'
         AS cleanup_token_matches \gset
\else
  \set cleanup_token_matches false
\endif

\if :apply_cleanup
\else
  \echo '拒绝执行：缺少 -v apply_cleanup=true。请先运行只读盘点脚本。'
  \quit 3
\endif

\if :cleanup_token_matches
\else
  \echo '拒绝执行：cleanup_confirmation 不匹配。'
  \quit 3
\endif

\echo '开始清理已精确识别的基金模拟/近似事实'
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

CREATE TEMP TABLE cleanup_target_funds (
    canonical_name TEXT PRIMARY KEY
) ON COMMIT DROP;

INSERT INTO cleanup_target_funds (canonical_name) VALUES
    ('001632 天弘中证食品饮料ETF联接C'),
    ('012733 易方达中证人工智能主题ETF联接A'),
    ('011103 天弘中证光伏产业指数C'),
    ('000689 前海开源新经济混合A'),
    ('008552 东财医药C'),
    ('161726 招商国证生物医药指数(LOF)A'),
    ('161725 招商中证白酒指数(LOF)A'),
    ('070019 嘉实价值优势混合A'),
    ('002593 富国美丽中国混合A'),
    ('008295 朱雀企业优胜C'),
    ('004476 景顺长城沪港深领先科技股票A'),
    ('001705 泓德战略转型股票'),
    ('005275 中欧创新成长灵活配置混合A');

CREATE TEMP TABLE cleanup_target_pairs (
    fund_name TEXT NOT NULL,
    holding_name TEXT NOT NULL,
    weight_pct NUMERIC(5, 2) NOT NULL,
    PRIMARY KEY (fund_name, holding_name)
) ON COMMIT DROP;

INSERT INTO cleanup_target_pairs (fund_name, holding_name, weight_pct) VALUES
    ('161725 招商中证白酒指数(LOF)A', '贵州茅台', 55.00),
    ('161725 招商中证白酒指数(LOF)A', '五粮液', 28.00),
    ('161725 招商中证白酒指数(LOF)A', '泸州老窖', 12.00),
    ('161725 招商中证白酒指数(LOF)A', '山西汾酒', 3.00),
    ('161725 招商中证白酒指数(LOF)A', '古井贡酒', 2.00),
    ('161726 招商国证生物医药指数(LOF)A', '迈瑞医疗', 12.00),
    ('161726 招商国证生物医药指数(LOF)A', '药明康德', 11.00),
    ('161726 招商国证生物医药指数(LOF)A', '恒瑞医药', 10.00),
    ('161726 招商国证生物医药指数(LOF)A', '爱尔眼科', 7.00),
    ('161726 招商国证生物医药指数(LOF)A', '智飞生物', 6.00),
    ('001632 天弘中证食品饮料ETF联接C', '贵州茅台', 25.00),
    ('001632 天弘中证食品饮料ETF联接C', '五粮液', 14.00),
    ('001632 天弘中证食品饮料ETF联接C', '海天味业', 8.00),
    ('001632 天弘中证食品饮料ETF联接C', '伊利股份', 8.00),
    ('001632 天弘中证食品饮料ETF联接C', '泸州老窖', 7.00),
    ('011103 天弘中证光伏产业指数C', '隆基绿能', 14.00),
    ('011103 天弘中证光伏产业指数C', '通威股份', 11.00),
    ('011103 天弘中证光伏产业指数C', 'TCL中环', 9.00),
    ('011103 天弘中证光伏产业指数C', '阳光电源', 9.00),
    ('011103 天弘中证光伏产业指数C', '晶澳科技', 7.00),
    ('012733 易方达中证人工智能主题ETF联接A', '科大讯飞', 11.00),
    ('012733 易方达中证人工智能主题ETF联接A', '海康威视', 10.00),
    ('012733 易方达中证人工智能主题ETF联接A', '中科曙光', 7.00),
    ('012733 易方达中证人工智能主题ETF联接A', '金山办公', 5.00),
    ('012733 易方达中证人工智能主题ETF联接A', '紫光股份', 4.00),
    ('008552 东财医药C', '恒瑞医药', 12.00),
    ('008552 东财医药C', '迈瑞医疗', 10.00),
    ('008552 东财医药C', '药明康德', 8.00),
    ('008552 东财医药C', '爱尔眼科', 6.00),
    ('008552 东财医药C', '智飞生物', 5.00);

\echo '清理前候选数量'
SELECT count(*) AS approximate_lookthrough_candidates
  FROM market.fund_lookthrough look
  JOIN reference.instruments fund ON fund.id = look.fund_instrument_id
  JOIN reference.instruments holding ON holding.id = look.holding_instrument_id
  JOIN cleanup_target_pairs target
    ON target.fund_name = fund.canonical_name
   AND target.holding_name = holding.canonical_name
   AND target.weight_pct = look.weight_pct
 WHERE look.report_period = DATE '2026-06-30'
   AND look.source_id IS NULL
   AND look.metadata ->> 'source' = 'index-disclosure'
   AND look.metadata ->> 'warning' LIKE '%估值近似%';

SELECT count(*) AS ungoverned_open_profile_candidates
  FROM reference.fund_profiles profile
  JOIN reference.instruments instrument ON instrument.id = profile.instrument_id
  JOIN cleanup_target_funds target ON target.canonical_name = instrument.canonical_name
 WHERE profile.source_id IS NULL
   AND profile.purchase_status = 'OPEN'
   AND profile.redemption_status = 'OPEN'
   AND profile.effective_to IS NULL
   AND profile.created_at >= TIMESTAMPTZ '2026-09-03 00:00:00+08'
   AND profile.effective_from =
       (profile.created_at AT TIME ZONE 'Asia/Shanghai')::date;

SELECT count(*) AS ungoverned_default_cutoff_candidates
  FROM reference.instrument_cutoff_rules rule
  JOIN reference.instruments instrument ON instrument.id = rule.instrument_id
  JOIN cleanup_target_funds target ON target.canonical_name = instrument.canonical_name
 WHERE rule.source_id IS NULL
   AND rule.sales_channel = 'DEFAULT'
   AND rule.cutoff_time = TIME '15:00'
   AND rule.timezone = 'Asia/Shanghai'
   AND rule.rule_version = 1
   AND rule.effective_to IS NULL
   AND rule.created_at >= TIMESTAMPTZ '2026-09-03 00:00:00+08'
   AND rule.effective_from =
       (rule.created_at AT TIME ZONE 'Asia/Shanghai')::date;

DO $$
DECLARE
    lookthrough_count INTEGER;
    profile_count INTEGER;
    cutoff_count INTEGER;
BEGIN
    SELECT count(*) INTO lookthrough_count
      FROM market.fund_lookthrough look
      JOIN reference.instruments fund ON fund.id = look.fund_instrument_id
      JOIN reference.instruments holding ON holding.id = look.holding_instrument_id
      JOIN cleanup_target_pairs target
        ON target.fund_name = fund.canonical_name
       AND target.holding_name = holding.canonical_name
       AND target.weight_pct = look.weight_pct
     WHERE look.report_period = DATE '2026-06-30'
       AND look.source_id IS NULL
       AND look.metadata ->> 'source' = 'index-disclosure'
       AND look.metadata ->> 'warning' LIKE '%估值近似%';

    SELECT count(*) INTO profile_count
      FROM reference.fund_profiles profile
      JOIN reference.instruments instrument ON instrument.id = profile.instrument_id
      JOIN cleanup_target_funds target
        ON target.canonical_name = instrument.canonical_name
     WHERE profile.source_id IS NULL
       AND profile.purchase_status = 'OPEN'
       AND profile.redemption_status = 'OPEN'
       AND profile.effective_to IS NULL
       AND profile.created_at >= TIMESTAMPTZ '2026-09-03 00:00:00+08'
       AND profile.effective_from =
           (profile.created_at AT TIME ZONE 'Asia/Shanghai')::date;

    SELECT count(*) INTO cutoff_count
      FROM reference.instrument_cutoff_rules rule
      JOIN reference.instruments instrument ON instrument.id = rule.instrument_id
      JOIN cleanup_target_funds target
        ON target.canonical_name = instrument.canonical_name
     WHERE rule.source_id IS NULL
       AND rule.sales_channel = 'DEFAULT'
       AND rule.cutoff_time = TIME '15:00'
       AND rule.timezone = 'Asia/Shanghai'
       AND rule.rule_version = 1
       AND rule.effective_to IS NULL
       AND rule.created_at >= TIMESTAMPTZ '2026-09-03 00:00:00+08'
       AND rule.effective_from =
           (rule.created_at AT TIME ZONE 'Asia/Shanghai')::date;

    IF lookthrough_count > 30 OR profile_count > 13 OR cutoff_count > 13 THEN
        RAISE EXCEPTION
            '候选数超过安全上限：lookthrough=%, profiles=%, cutoffs=%',
            lookthrough_count, profile_count, cutoff_count;
    END IF;
END;
$$;

WITH removed AS (
    DELETE FROM market.fund_lookthrough look
    USING reference.instruments fund,
          reference.instruments holding,
          cleanup_target_pairs target
     WHERE fund.id = look.fund_instrument_id
       AND holding.id = look.holding_instrument_id
       AND target.fund_name = fund.canonical_name
       AND target.holding_name = holding.canonical_name
       AND target.weight_pct = look.weight_pct
       AND look.report_period = DATE '2026-06-30'
       AND look.source_id IS NULL
       AND look.metadata ->> 'source' = 'index-disclosure'
       AND look.metadata ->> 'warning' LIKE '%估值近似%'
    RETURNING look.id
)
SELECT count(*) AS removed_approximate_lookthrough_rows FROM removed;

WITH removed AS (
    DELETE FROM reference.fund_profiles profile
    USING reference.instruments instrument, cleanup_target_funds target
     WHERE instrument.id = profile.instrument_id
       AND target.canonical_name = instrument.canonical_name
       AND profile.source_id IS NULL
       AND profile.purchase_status = 'OPEN'
       AND profile.redemption_status = 'OPEN'
       AND profile.effective_to IS NULL
       AND profile.created_at >= TIMESTAMPTZ '2026-09-03 00:00:00+08'
       AND profile.effective_from =
           (profile.created_at AT TIME ZONE 'Asia/Shanghai')::date
    RETURNING profile.id
)
SELECT count(*) AS removed_ungoverned_open_profiles FROM removed;

WITH removed AS (
    DELETE FROM reference.instrument_cutoff_rules rule
    USING reference.instruments instrument, cleanup_target_funds target
     WHERE instrument.id = rule.instrument_id
       AND target.canonical_name = instrument.canonical_name
       AND rule.source_id IS NULL
       AND rule.sales_channel = 'DEFAULT'
       AND rule.cutoff_time = TIME '15:00'
       AND rule.timezone = 'Asia/Shanghai'
       AND rule.rule_version = 1
       AND rule.effective_to IS NULL
       AND rule.created_at >= TIMESTAMPTZ '2026-09-03 00:00:00+08'
       AND rule.effective_from =
           (rule.created_at AT TIME ZONE 'Asia/Shanghai')::date
    RETURNING rule.id
)
SELECT count(*) AS removed_ungoverned_default_cutoffs FROM removed;

COMMIT;

\echo '清理完成。请立即重新执行 audit_legacy_fund_mock_data.sql 回读。'
