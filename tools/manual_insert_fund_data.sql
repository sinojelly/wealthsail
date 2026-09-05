-- =====================================================================
-- 手工补齐 12 只场外基金完整参考数据（不依赖 git pull / 推送）
-- 用法（Supabase psql 或任意 psql 会话）：
--   psql "$MIGRATION_DATABASE_URL" -f tools/manual_insert_fund_data.sql
--
-- 特点：
--   * 全程幂等（NOT EXISTS / ON CONFLICT DO NOTHING），可重复执行；
--   * 与迁移 052/054/055 的 canonical_name 严格同口径，之后正式跑
--     migrate 不会产生重复数据；
--   * 开头先输出五张表现状（诊断），结尾再输出补齐后的行数（验证）。
-- =====================================================================

\echo '== 执行前诊断：五张表现状 =='
SELECT
  (SELECT count(*) FROM reference.instruments
    WHERE asset_type = 'OFF_EXCHANGE_FUND')                          AS otc_fund_instruments,
  (SELECT count(*) FROM reference.instrument_aliases a
    JOIN reference.instruments i ON i.id = a.instrument_id
    WHERE a.alias_type = 'TICKER'
      AND i.asset_type = 'OFF_EXCHANGE_FUND')                        AS fund_ticker_aliases,
  (SELECT count(*) FROM reference.fund_profiles)                     AS fund_profiles,
  (SELECT count(*) FROM reference.instrument_cutoff_rules
     WHERE sales_channel = 'DEFAULT')                                AS cutoff_rules_default,
  (SELECT count(*) FROM market.fund_lookthrough)                     AS lookthrough_rows;

BEGIN;

-- ---------- 会话级临时表：基金 / 持仓股票 / 持仓权重 ----------
CREATE TEMP TABLE _funds (code text, cname text) ON COMMIT DROP;
INSERT INTO _funds (code, cname) VALUES
    ('001632', '001632 天弘中证食品饮料ETF联接C'),
    ('012733', '012733 易方达中证人工智能主题ETF联接A'),
    ('011103', '011103 天弘中证光伏产业指数C'),
    ('000689', '000689 前海开源新经济混合A'),
    ('008552', '008552 东财医药C'),
    ('161726', '161726 招商国证生物医药指数(LOF)A'),
    ('161725', '161725 招商中证白酒指数(LOF)A'),
    ('070019', '070019 嘉实价值优势混合A'),
    ('002593', '002593 富国美丽中国混合A'),
    ('008295', '008295 朱雀企业优胜C'),
    ('004476', '004476 景顺长城沪港深领先科技股票A'),
    ('001705', '001705 泓德战略转型股票'),
    ('005275', '005275 中欧创新成长灵活配置混合A');

CREATE TEMP TABLE _stocks (code text, cname text, mkt text) ON COMMIT DROP;
INSERT INTO _stocks (code, cname, mkt) VALUES
    ('600519', '贵州茅台', 'SSE'),
    ('000858', '五粮液', 'SZSE'),
    ('000568', '泸州老窖', 'SZSE'),
    ('600809', '山西汾酒', 'SSE'),
    ('000596', '古井贡酒', 'SZSE'),
    ('300760', '迈瑞医疗', 'SZSE'),
    ('603259', '药明康德', 'SSE'),
    ('600276', '恒瑞医药', 'SSE'),
    ('300015', '爱尔眼科', 'SZSE'),
    ('300122', '智飞生物', 'SZSE'),
    ('603288', '海天味业', 'SSE'),
    ('600887', '伊利股份', 'SSE'),
    ('601012', '隆基绿能', 'SSE'),
    ('600438', '通威股份', 'SSE'),
    ('002129', 'TCL中环', 'SZSE'),
    ('300274', '阳光电源', 'SZSE'),
    ('002459', '晶澳科技', 'SZSE'),
    ('002230', '科大讯飞', 'SZSE'),
    ('002415', '海康威视', 'SZSE'),
    ('603019', '中科曙光', 'SSE'),
    ('688111', '金山办公', 'SSE'),
    ('000938', '紫光股份', 'SZSE');

CREATE TEMP TABLE _lt (fund text, hold text, w numeric) ON COMMIT DROP;
INSERT INTO _lt (fund, hold, w) VALUES
    -- 161725 招商中证白酒指数 LOF
    ('161725', '600519', 55.00), ('161725', '000858', 28.00),
    ('161725', '000568', 12.00), ('161725', '600809',  3.00),
    ('161725', '000596',  2.00),
    -- 161726 招商国证生物医药指数 LOF
    ('161726', '300760', 12.00), ('161726', '603259', 11.00),
    ('161726', '600276', 10.00), ('161726', '300015',  7.00),
    ('161726', '300122',  6.00),
    -- 001632 天弘中证食品饮料 ETF 联接 C
    ('001632', '600519', 25.00), ('001632', '000858', 14.00),
    ('001632', '603288',  8.00), ('001632', '600887',  8.00),
    ('001632', '000568',  7.00),
    -- 011103 天弘中证光伏产业指数 C
    ('011103', '601012', 14.00), ('011103', '600438', 11.00),
    ('011103', '002129',  9.00), ('011103', '300274',  9.00),
    ('011103', '002459',  7.00),
    -- 012733 易方达中证人工智能主题 ETF 联接 A
    ('012733', '002230', 11.00), ('012733', '002415', 10.00),
    ('012733', '603019',  7.00), ('012733', '688111',  5.00),
    ('012733', '000938',  4.00),
    -- 008552 东财医药 C（中证医药卫生指数近似）
    ('008552', '600276', 12.00), ('008552', '300760', 10.00),
    ('008552', '603259',  8.00), ('008552', '300015',  6.00),
    ('008552', '300122',  5.00);

-- ---------- 1) 注册缺失的基金 / 股票标的 ----------
INSERT INTO reference.instruments (
    asset_type, primary_market, trading_currency, canonical_name,
    lifecycle_status, metadata)
SELECT 'OFF_EXCHANGE_FUND', 'FUND_CN', 'CNY', f.cname, 'ACTIVE',
       jsonb_build_object('registeredBy', 'manual-fix', 'source', 'manual-2026-09-05')
  FROM _funds f
 WHERE NOT EXISTS (SELECT 1 FROM reference.instruments i
                    WHERE i.canonical_name = f.cname);

INSERT INTO reference.instruments (
    asset_type, primary_market, trading_currency, canonical_name,
    lifecycle_status, metadata)
SELECT 'CN_STOCK', s.mkt, 'CNY', s.cname, 'ACTIVE',
       jsonb_build_object('registeredBy', 'manual-fix', 'source', 'manual-2026-09-05')
  FROM _stocks s
 WHERE NOT EXISTS (SELECT 1 FROM reference.instruments i
                    WHERE i.canonical_name = s.cname);

-- ---------- 2) 补 TICKER 别名 ----------
INSERT INTO reference.instrument_aliases (
    instrument_id, alias_type, alias_value, market, effective_from)
SELECT i.id, 'TICKER', f.code, 'FUND_CN', CURRENT_DATE
  FROM _funds f
  JOIN reference.instruments i ON i.canonical_name = f.cname
ON CONFLICT (alias_type, alias_value, market, effective_from) DO NOTHING;

INSERT INTO reference.instrument_aliases (
    instrument_id, alias_type, alias_value, market, effective_from)
SELECT i.id, 'TICKER', s.code, s.mkt, CURRENT_DATE
  FROM _stocks s
  JOIN reference.instruments i ON i.canonical_name = s.cname
   AND i.asset_type = 'CN_STOCK'
ON CONFLICT (alias_type, alias_value, market, effective_from) DO NOTHING;

-- ---------- 3) 申赎状态 fund_profiles（OPEN/OPEN） ----------
INSERT INTO reference.fund_profiles (
    instrument_id, purchase_status, redemption_status, effective_from)
SELECT i.id, 'OPEN', 'OPEN', CURRENT_DATE
  FROM _funds f
  JOIN reference.instruments i ON i.canonical_name = f.cname
ON CONFLICT (instrument_id, effective_from) DO NOTHING;

-- ---------- 4) 申赎截止 instrument_cutoff_rules（默认渠道 15:00） ----------
INSERT INTO reference.instrument_cutoff_rules (
    instrument_id, sales_channel, cutoff_time, timezone,
    effective_from, rule_version)
SELECT i.id, 'DEFAULT', TIME '15:00', 'Asia/Shanghai', CURRENT_DATE, 1
  FROM _funds f
  JOIN reference.instruments i ON i.canonical_name = f.cname
ON CONFLICT (instrument_id, sales_channel, effective_from, rule_version)
DO NOTHING;

-- ---------- 5) 持仓穿透 fund_lookthrough（6 只指数/ETF 联接基金，30 行） ----------
INSERT INTO market.fund_lookthrough (
    fund_instrument_id, holding_instrument_id, weight_pct,
    report_period, metadata, as_of)
SELECT fa.instrument_id, ha.instrument_id, lt.w, DATE '2026-06-30',
       jsonb_build_object(
           'source', 'index-disclosure',
           'warning', '基于公开指数机制的估值近似，真季报披露后请用 scripts/ingest_fund_holdings.py 覆盖'),
       now()
  FROM _lt lt
  JOIN reference.instrument_aliases fa
    ON fa.alias_type = 'TICKER' AND fa.alias_value = lt.fund
  JOIN reference.instrument_aliases ha
    ON ha.alias_type = 'TICKER' AND ha.alias_value = lt.hold
ON CONFLICT (fund_instrument_id, holding_instrument_id, report_period)
DO NOTHING;

COMMIT;

\echo '== 执行后验证：应为 13/13/13/13/13 基金相关 + 30 持仓行 =='
SELECT
  (SELECT count(*) FROM reference.instruments
    WHERE asset_type = 'OFF_EXCHANGE_FUND')                          AS otc_fund_instruments,
  (SELECT count(DISTINCT a.instrument_id)
     FROM reference.instrument_aliases a
     WHERE a.alias_type = 'TICKER'
       AND a.alias_value IN ('001632','012733','011103','000689','008552',
                             '161726','161725','070019','002593','008295',
                             '004476','001705','005275'))            AS funds_with_ticker,
  (SELECT count(DISTINCT fp.instrument_id)
     FROM reference.fund_profiles fp
     JOIN reference.instruments i ON i.id = fp.instrument_id
     WHERE i.asset_type = 'OFF_EXCHANGE_FUND')                       AS funds_with_profile,
  (SELECT count(DISTINCT cr.instrument_id)
     FROM reference.instrument_cutoff_rules cr
     JOIN reference.instruments i ON i.id = cr.instrument_id
     WHERE i.asset_type = 'OFF_EXCHANGE_FUND')                       AS funds_with_cutoff,
  (SELECT count(DISTINCT lt.fund_instrument_id)
     FROM market.fund_lookthrough lt)                                AS funds_with_lookthrough,
  (SELECT count(*) FROM market.fund_lookthrough)                     AS lookthrough_rows;
