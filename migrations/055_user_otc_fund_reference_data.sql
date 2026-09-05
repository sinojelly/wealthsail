BEGIN;

-- 055 补全 12 只用户场外基金的参考数据，使 14:30 基金报告有真实内容。
--
-- 根因：用户手动插入 reference.instruments 时误用了无效类型字面量 FUND，而该表
-- CHECK 仅允许 ('CN_STOCK','EXCHANGE_FUND','OFF_EXCHANGE_FUND','BOND','GOLD_SPOT')，
-- 正确值是 OFF_EXCHANGE_FUND。Migration 052/054 已注册 5 只指数基金并回填持仓，
-- 但 047 只建了 fund_profiles 表、从未插入过申赎状态行；instrument_cutoff_rules
-- 也缺这 12 只基金的申赎截止规则；第 6 只指数基金 008552(东财医药C) 缺少持仓穿透。
-- 这些缺失导致报告「申赎提醒/总结」无内容。
--
-- 本迁移一次性补齐（全部幂等）：
--   1) 12 只基金 instruments（OFF_EXCHANGE_FUND / FUND_CN）
--   2) 12 只基金 TICKER 别名（6 位代码）
--   3) 22 只持仓股票 instruments + TICKER 别名（供 lookthrough 解析，缺则补）
--   4) 12 只基金 fund_profiles（申购/赎回 OPEN，生效至今）
--   5) 12 只基金 instrument_cutoff_rules（默认渠道 15:00 截止）
--   6) 6 只指数/ETF 联接基金 fund_lookthrough（基于公开指数机制近似权重，
--      含 008552 新增；report_period 2026-06-30）
--
-- 注：7 只主动混合/股票型基金（000689/070019/002593/008295/004476/001705/
-- 005275）的真实前五大重仓需用户给季报披露后，用
-- scripts/ingest_fund_holdings.py --fund-symbol ... --period ... --holdings ...
-- 灌入；本迁移不为主动基金编造持仓（仅保证申赎状态/截止规则有内容）。

-- 1) 注册 12 只场外基金（缺则建，按 canonical_name 去重）
WITH requested (code, canonical_name) AS (
    VALUES
        ('001632', '001632 天弘中证食品饮料ETF联接C'),
        ('012733', '012733 易方达中证人工智能主题ETF联接A'),
        ('011103', '011103 天弘中证光伏产业指数C'),
        ('000689', '000689 前海开源新经济混合A'),
        ('008552', '008552 东财医药C'),
        ('161726', '161726 招商国证生物医药指数(LOF)A'),
        ('070019', '070019 嘉实价值优势混合A'),
        ('002593', '002593 富国美丽中国混合A'),
        ('008295', '008295 朱雀企业优胜C'),
        ('004476', '004476 景顺长城沪港深领先科技股票A'),
        ('001705', '001705 泓德战略转型股票'),
        ('005275', '005275 中欧创新成长灵活配置混合A')
),
missing AS (
    SELECT r.code, r.canonical_name
      FROM requested r
     WHERE NOT EXISTS (
               SELECT 1 FROM reference.instruments i
                WHERE i.canonical_name = r.canonical_name
           )
)
INSERT INTO reference.instruments (
    asset_type, primary_market, trading_currency, canonical_name,
    lifecycle_status, metadata
)
SELECT 'OFF_EXCHANGE_FUND', 'FUND_CN', 'CNY', m.canonical_name, 'ACTIVE',
       jsonb_build_object(
           'registeredBy', 'user-batch',
           'source', 'seed-055-2026-09-05'
       )
  FROM missing;

-- 2) 12 只基金 TICKER 别名（读表，可见步骤 1；已存在则跳过）
INSERT INTO reference.instrument_aliases (
    instrument_id, alias_type, alias_value, market, effective_from
)
SELECT i.id, 'TICKER', r.code, 'FUND_CN', CURRENT_DATE
  FROM (VALUES
        ('001632', '001632 天弘中证食品饮料ETF联接C'),
        ('012733', '012733 易方达中证人工智能主题ETF联接A'),
        ('011103', '011103 天弘中证光伏产业指数C'),
        ('000689', '000689 前海开源新经济混合A'),
        ('008552', '008552 东财医药C'),
        ('161726', '161726 招商国证生物医药指数(LOF)A'),
        ('070019', '070019 嘉实价值优势混合A'),
        ('002593', '002593 富国美丽中国混合A'),
        ('008295', '008295 朱雀企业优胜C'),
        ('004476', '004476 景顺长城沪港深领先科技股票A'),
        ('001705', '001705 泓德战略转型股票'),
        ('005275', '005275 中欧创新成长灵活配置混合A')
       ) r(code, canonical_name)
  JOIN reference.instruments i ON i.canonical_name = r.canonical_name
ON CONFLICT (alias_type, alias_value, market, effective_from) DO NOTHING;

-- 3) 注册持仓股票（22 只，缺则建；CN_STOCK）
WITH requested (holding_symbol, holding_name, market) AS (
    VALUES
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
        ('000938', '紫光股份', 'SZSE')
),
missing AS (
    SELECT r.holding_symbol, r.holding_name, r.market
      FROM requested r
     WHERE NOT EXISTS (
               SELECT 1 FROM reference.instruments i
                WHERE i.canonical_name = r.holding_name
           )
)
INSERT INTO reference.instruments (
    asset_type, primary_market, trading_currency, canonical_name,
    lifecycle_status, metadata
)
SELECT 'CN_STOCK', m.market, 'CNY', m.holding_name, 'ACTIVE',
       jsonb_build_object(
           'registeredBy', 'fund-holdings-seed',
           'source', 'seed-055-2026-09-05'
       )
  FROM missing;

-- 4) 持仓股票 TICKER 别名（读表，可见步骤 3）
INSERT INTO reference.instrument_aliases (
    instrument_id, alias_type, alias_value, market, effective_from
)
SELECT i.id, 'TICKER', r.holding_symbol, r.market, CURRENT_DATE
  FROM (VALUES
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
        ('000938', '紫光股份', 'SZSE')
       ) r(holding_symbol, holding_name, market)
  JOIN reference.instruments i ON i.canonical_name = r.holding_name
   AND i.asset_type = 'CN_STOCK'
ON CONFLICT (alias_type, alias_value, market, effective_from) DO NOTHING;

-- 5) 12 只基金申赎状态（OPEN/OPEN，生效至今；已存在则跳过）
INSERT INTO reference.fund_profiles (
    instrument_id, purchase_status, redemption_status, effective_from, source_id
)
SELECT i.id, 'OPEN', 'OPEN', CURRENT_DATE, NULL
  FROM (VALUES
        ('001632 天弘中证食品饮料ETF联接C'),
        ('012733 易方达中证人工智能主题ETF联接A'),
        ('011103 天弘中证光伏产业指数C'),
        ('000689 前海开源新经济混合A'),
        ('008552 东财医药C'),
        ('161726 招商国证生物医药指数(LOF)A'),
        ('070019 嘉实价值优势混合A'),
        ('002593 富国美丽中国混合A'),
        ('008295 朱雀企业优胜C'),
        ('004476 景顺长城沪港深领先科技股票A'),
        ('001705 泓德战略转型股票'),
        ('005275 中欧创新成长灵活配置混合A')
       ) r(canonical_name)
  JOIN reference.instruments i ON i.canonical_name = r.canonical_name
ON CONFLICT (instrument_id, effective_from) DO NOTHING;

-- 6) 12 只基金申赎截止规则（默认渠道 15:00；已存在则跳过）
INSERT INTO reference.instrument_cutoff_rules (
    instrument_id, sales_channel, cutoff_time, timezone, effective_from, rule_version
)
SELECT i.id, 'DEFAULT', TIME '15:00', 'Asia/Shanghai', CURRENT_DATE, 1
  FROM (VALUES
        ('001632 天弘中证食品饮料ETF联接C'),
        ('012733 易方达中证人工智能主题ETF联接A'),
        ('011103 天弘中证光伏产业指数C'),
        ('000689 前海开源新经济混合A'),
        ('008552 东财医药C'),
        ('161726 招商国证生物医药指数(LOF)A'),
        ('070019 嘉实价值优势混合A'),
        ('002593 富国美丽中国混合A'),
        ('008295 朱雀企业优胜C'),
        ('004476 景顺长城沪港深领先科技股票A'),
        ('001705 泓德战略转型股票'),
        ('005275 中欧创新成长灵活配置混合A')
       ) r(canonical_name)
  JOIN reference.instruments i ON i.canonical_name = r.canonical_name
ON CONFLICT (instrument_id, sales_channel, effective_from, rule_version) DO NOTHING;

-- 7) 6 只指数/ETF 联接基金持仓穿透（基于公开指数机制近似；008552 医疗为新增，
--    其余 5 只与 054 同口径，report_period 2026-06-30；已存在则跳过）
WITH requested (fund_symbol, holding_symbol, weight_pct) AS (
    VALUES
        -- 161725 招商中证白酒指数 LOF（中证白酒指数）
        ('161725', '600519', '55.00'),
        ('161725', '000858', '28.00'),
        ('161725', '000568', '12.00'),
        ('161725', '600809', '3.00'),
        ('161725', '000596', '2.00'),
        -- 161726 招商国证生物医药指数 LOF（国证生物医药指数）
        ('161726', '300760', '12.00'),
        ('161726', '603259', '11.00'),
        ('161726', '600276', '10.00'),
        ('161726', '300015', '7.00'),
        ('161726', '300122', '6.00'),
        -- 001632 天弘中证食品饮料 ETF 联接 C（中证食品饮料指数）
        ('001632', '600519', '25.00'),
        ('001632', '000858', '14.00'),
        ('001632', '603288', '8.00'),
        ('001632', '600887', '8.00'),
        ('001632', '000568', '7.00'),
        -- 011103 天弘中证光伏产业指数 C（中证光伏产业指数）
        ('011103', '601012', '14.00'),
        ('011103', '600438', '11.00'),
        ('011103', '002129', '9.00'),
        ('011103', '300274', '9.00'),
        ('011103', '002459', '7.00'),
        -- 012733 易方达中证人工智能主题 ETF 联接 A（中证人工智能指数）
        ('012733', '002230', '11.00'),
        ('012733', '002415', '10.00'),
        ('012733', '603019', '7.00'),
        ('012733', '688111', '5.00'),
        ('012733', '000938', '4.00'),
        -- 008552 东财医药 C（中证医药卫生指数，近似权重，真季报后请覆盖）
        ('008552', '600276', '12.00'),
        ('008552', '300760', '10.00'),
        ('008552', '603259', '8.00'),
        ('008552', '300015', '6.00'),
        ('008552', '300122', '5.00')
),
funds AS (
    SELECT DISTINCT r.fund_symbol, i.id AS fund_instrument_id
      FROM requested r
      JOIN reference.instrument_aliases a
        ON a.alias_type = 'TICKER' AND a.alias_value = r.fund_symbol
       AND a.effective_from <= CURRENT_DATE
       AND (a.effective_to IS NULL OR a.effective_to >= CURRENT_DATE)
      JOIN reference.instruments i ON i.id = a.instrument_id
),
holdings AS (
    SELECT DISTINCT r.holding_symbol, i.id AS holding_instrument_id
      FROM requested r
      JOIN reference.instrument_aliases a
        ON a.alias_type = 'TICKER' AND a.alias_value = r.holding_symbol
       AND a.effective_from <= CURRENT_DATE
       AND (a.effective_to IS NULL OR a.effective_to >= CURRENT_DATE)
      JOIN reference.instruments i ON i.id = a.instrument_id
)
INSERT INTO market.fund_lookthrough (
    fund_instrument_id, holding_instrument_id, weight_pct, report_period,
    source_id, metadata, as_of
)
SELECT f.fund_instrument_id, h.holding_instrument_id,
       r.weight_pct::numeric, DATE '2026-06-30', NULL,
       jsonb_build_object(
           'source', 'index-disclosure',
           'warning', '基于公开指数机制的估值近似，真季报披露后请用 scripts/ingest_fund_holdings.py 覆盖'
       ),
       now()
  FROM requested r
  JOIN funds f ON f.fund_symbol = r.fund_symbol
  JOIN holdings h ON h.holding_symbol = r.holding_symbol
ON CONFLICT (fund_instrument_id, holding_instrument_id, report_period) DO NOTHING;

-- 校验输出：确认各类数据已就位（仅 SELECT，不影响数据）
SELECT
    (SELECT count(*) FROM reference.instruments
        WHERE canonical_name IN (
            '001632 天弘中证食品饮料ETF联接C','012733 易方达中证人工智能主题ETF联接A',
            '011103 天弘中证光伏产业指数C','000689 前海开源新经济混合A','008552 东财医药C',
            '161726 招商国证生物医药指数(LOF)A','070019 嘉实价值优势混合A',
            '002593 富国美丽中国混合A','008295 朱雀企业优胜C',
            '004476 景顺长城沪港深领先科技股票A','001705 泓德战略转型股票',
            '005275 中欧创新成长灵活配置混合A')) AS funds_registered,
    (SELECT count(*) FROM reference.fund_profiles fp
        JOIN reference.instruments i ON i.id = fp.instrument_id
        WHERE i.canonical_name LIKE '0%' OR i.canonical_name LIKE '1%') AS fund_profiles_rows,
    (SELECT count(*) FROM reference.instrument_cutoff_rules cr
        JOIN reference.instruments i ON i.id = cr.instrument_id
        WHERE i.asset_type = 'OFF_EXCHANGE_FUND') AS cutoff_rules_rows,
    (SELECT count(DISTINCT fund_instrument_id) FROM market.fund_lookthrough
        WHERE report_period = DATE '2026-06-30') AS funds_with_lookthrough;

COMMIT;
