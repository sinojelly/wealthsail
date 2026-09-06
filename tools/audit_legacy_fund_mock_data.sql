-- WealthSail 基金模拟/近似数据只读盘点。
-- 必须使用 MIGRATION_DATABASE_URL（migration owner），不能使用 API DATABASE_URL。
-- 执行：psql "$MIGRATION_DATABASE_URL" -X -v ON_ERROR_STOP=1 \
--         -f tools/audit_legacy_fund_mock_data.sql

\set ON_ERROR_STOP on
\pset pager off
\echo '== 只读盘点：不会修改任何数据 =='

BEGIN TRANSACTION READ ONLY;

\echo '1. 历史迁移：055 不应出现；052～054 可能已经执行'
SELECT version, filename, applied_at
  FROM public.wealthsail_schema_migrations
 WHERE version BETWEEN 51 AND 55
 ORDER BY version;

\echo '2. 旧 fund_lookthrough 总量与可证明的近似行'
SELECT count(*) AS all_lookthrough_rows,
       count(*) FILTER (
           WHERE metadata ->> 'source' = 'index-disclosure'
             AND metadata ->> 'warning' LIKE '%估值近似%'
       ) AS approximate_rows,
       count(*) FILTER (WHERE source_id IS NOT NULL) AS sourced_rows
  FROM market.fund_lookthrough;

\echo '3. 逐行检查近似穿透权重（清理候选）'
SELECT fund_alias.alias_value AS fund_code,
       fund.canonical_name AS fund_name,
       holding_alias.alias_value AS holding_code,
       holding.canonical_name AS holding_name,
       look.weight_pct,
       look.report_period,
       look.source_id,
       look.metadata,
       look.created_at
  FROM market.fund_lookthrough look
  JOIN reference.instruments fund ON fund.id = look.fund_instrument_id
  JOIN reference.instruments holding ON holding.id = look.holding_instrument_id
  LEFT JOIN LATERAL (
      SELECT alias_value
        FROM reference.instrument_aliases
       WHERE instrument_id = fund.id AND alias_type = 'TICKER'
       ORDER BY effective_from DESC
       LIMIT 1
  ) fund_alias ON TRUE
  LEFT JOIN LATERAL (
      SELECT alias_value
        FROM reference.instrument_aliases
       WHERE instrument_id = holding.id AND alias_type = 'TICKER'
       ORDER BY effective_from DESC
       LIMIT 1
  ) holding_alias ON TRUE
 WHERE look.metadata ->> 'source' = 'index-disclosure'
   AND look.metadata ->> 'warning' LIKE '%估值近似%'
 ORDER BY fund_code, holding_code, look.report_period;

\echo '4. 基金/股票主数据的登记来源；这些是真实实体，不是删除候选'
SELECT COALESCE(metadata ->> 'source', '<none>') AS metadata_source,
       COALESCE(metadata ->> 'registeredBy', '<none>') AS registered_by,
       asset_type,
       count(*) AS row_count
  FROM reference.instruments
 WHERE metadata ? 'source' OR metadata ? 'registeredBy'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo '5. 目标基金中无来源的固定 OPEN/OPEN 状态（人工确认后方可清理）'
WITH target_funds(canonical_name) AS (
    VALUES
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
        ('005275 中欧创新成长灵活配置混合A')
)
SELECT profile.id, instrument.canonical_name,
       profile.purchase_status, profile.redemption_status,
       profile.effective_from, profile.effective_to,
       profile.source_id, profile.created_at
  FROM reference.fund_profiles profile
  JOIN reference.instruments instrument ON instrument.id = profile.instrument_id
  JOIN target_funds target ON target.canonical_name = instrument.canonical_name
 WHERE profile.source_id IS NULL
   AND profile.purchase_status = 'OPEN'
   AND profile.redemption_status = 'OPEN'
 ORDER BY instrument.canonical_name, profile.effective_from;

\echo '6. 目标基金中无来源的默认 15:00 规则（人工确认后方可清理）'
WITH target_funds(canonical_name) AS (
    VALUES
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
        ('005275 中欧创新成长灵活配置混合A')
)
SELECT rule.id, instrument.canonical_name,
       rule.sales_channel, rule.cutoff_time, rule.timezone,
       rule.effective_from, rule.effective_to, rule.rule_version,
       rule.source_id, rule.created_at
  FROM reference.instrument_cutoff_rules rule
  JOIN reference.instruments instrument ON instrument.id = rule.instrument_id
  JOIN target_funds target ON target.canonical_name = instrument.canonical_name
 WHERE rule.source_id IS NULL
   AND rule.sales_channel = 'DEFAULT'
   AND rule.cutoff_time = TIME '15:00'
   AND rule.timezone = 'Asia/Shanghai'
   AND rule.rule_version = 1
 ORDER BY instrument.canonical_name, rule.effective_from;

\echo '7. 真实基金披露、正式净值与持久化估值覆盖'
SELECT 'fund_disclosure_components' AS fact_type,
       count(*) AS row_count,
       count(DISTINCT fund_instrument_id) AS subject_count
  FROM market.fund_disclosure_components
UNION ALL
SELECT 'official_nav', count(*), count(DISTINCT subject_id)
  FROM market.market_observations
 WHERE field_code = 'NAV' AND quality_status <> 'UNAVAILABLE'
UNION ALL
SELECT 'estimated_nav', count(*), count(DISTINCT subject_id)
  FROM market.market_observations
 WHERE field_code = 'ESTIMATED_NAV' AND quality_status <> 'UNAVAILABLE';

\echo '8. 真实估值按来源、方法和质量分组'
SELECT source.name AS source_name,
       observation.method_version,
       observation.quality_status,
       count(*) AS row_count,
       min(observation.as_of) AS earliest_as_of,
       max(observation.as_of) AS latest_as_of
  FROM market.market_observations observation
  LEFT JOIN reference.data_sources source ON source.id = observation.source_id
 WHERE observation.field_code IN ('NAV', 'ESTIMATED_NAV')
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo '9. 可能由旧近似链路生成的历史 14:30 报告；只审计，不自动清理'
SELECT id, tenant_id, trade_date, version, status, quality_status,
       created_at, left(summary, 240) AS summary_head
  FROM reporting.reports
 WHERE report_session = 'FUND_1430'
   AND (
       summary LIKE '%只持仓加权%'
       OR summary LIKE '%可正常申赎%'
       OR summary LIKE '%持仓数据已超过 3 个月%'
   )
 ORDER BY created_at DESC;

ROLLBACK;

\echo '== 盘点结束：事务已回滚，没有写入 =='
