BEGIN;

-- 053 的 INSERT 通过 alias JOIN 解析持仓股票；未注册的股票 JOIN 不到就
-- 被静默跳过，导致 053 显示 applied 但 fund_lookthrough 仍 0 行。
--
-- 关键约束（PostgreSQL）：**同一条语句内**的多个 data-modifying CTE 并发
-- 执行，彼此看不到对方的写入。因此「先注册股票 / 基金，再读 alias 表回
-- 填 lookthrough」必须拆成**多条顺序语句**——后一条语句在同一事务内可以
-- 看到前一条的写入。同一个 bug 也存在于 052（新注册基金拿不到 alias），
-- 本迁移一并兜底补 alias。
--
-- 步骤：
--   1) 注册缺失的持仓股票（22 只，CN_STOCK，canonical_name 用中文名）
--   2) 为这些股票补 TICKER alias（读表，可见步骤 1 的写入）
--   3) 注册缺失的指数基金（5 只，OFF_EXCHANGE_FUND/FUND_CN）
--   4) 为这些基金补 TICKER alias（读表，可见步骤 3 的写入）
--   5) 回填 lookthrough（读 alias 表，可见步骤 1-4 的全部写入）
-- 权重仍为基于公开指数机制的估算近似，metadata.warning 保留该标记。

-- 1) 注册缺失的持仓股票
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
               SELECT 1 FROM reference.instrument_aliases alias
                WHERE alias.alias_type = 'TICKER'
                  AND alias.alias_value = r.holding_symbol
                  AND alias.effective_from <= CURRENT_DATE
                  AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
           )
       AND NOT EXISTS (
               SELECT 1 FROM reference.instruments instrument
                WHERE instrument.canonical_name = r.holding_name
           )
)
INSERT INTO reference.instruments (
    asset_type, primary_market, trading_currency, canonical_name,
    lifecycle_status, metadata
)
SELECT 'CN_STOCK', m.market, 'CNY', m.holding_name, 'ACTIVE',
       jsonb_build_object(
           'registeredBy', 'fund-holdings-seed',
           'source', 'index-fund-holdings'
       )
  FROM missing m;

-- 2) 为这些股票补 TICKER alias（读表，可见步骤 1）
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
)
INSERT INTO reference.instrument_aliases (
    instrument_id, alias_type, alias_value, market, effective_from
)
SELECT instrument.id, 'TICKER', r.holding_symbol, r.market, CURRENT_DATE
  FROM requested r
  JOIN reference.instruments instrument
    ON instrument.canonical_name = r.holding_name
   AND instrument.asset_type = 'CN_STOCK'
 WHERE NOT EXISTS (
           SELECT 1 FROM reference.instrument_aliases alias
            WHERE alias.alias_type = 'TICKER'
              AND alias.alias_value = r.holding_symbol
              AND alias.effective_from <= CURRENT_DATE
              AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
       )
ON CONFLICT DO NOTHING;

-- 3) 注册缺失的指数基金（052 已建的行靠 canonical_name 去重，不重复建行）
WITH requested (fund_symbol, fund_name) AS (
    VALUES
        ('161725', '招商中证白酒指数(LOF)A'),
        ('161726', '招商国证生物医药指数(LOF)A'),
        ('001632', '天弘中证食品饮料ETF联接C'),
        ('011103', '天弘中证光伏产业指数C'),
        ('012733', '易方达中证人工智能主题ETF联接A')
),
missing AS (
    SELECT r.fund_symbol, r.fund_name
      FROM requested r
     WHERE NOT EXISTS (
               SELECT 1 FROM reference.instrument_aliases alias
                WHERE alias.alias_type = 'TICKER'
                  AND alias.alias_value = r.fund_symbol
                  AND alias.effective_from <= CURRENT_DATE
                  AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
           )
       AND NOT EXISTS (
               SELECT 1 FROM reference.instruments instrument
                WHERE instrument.canonical_name = r.fund_symbol || ' ' || r.fund_name
           )
)
INSERT INTO reference.instruments (
    asset_type, primary_market, trading_currency, canonical_name,
    lifecycle_status, metadata
)
SELECT 'OFF_EXCHANGE_FUND', 'FUND_CN', 'CNY',
       m.fund_symbol || ' ' || m.fund_name, 'ACTIVE',
       jsonb_build_object(
           'registeredBy', 'fund-holdings-seed',
           'source', 'index-fund-holdings'
       )
  FROM missing m;

-- 4) 为这些基金补 TICKER alias（读表，可见步骤 3；同时兜底 052 漏建的情况）
WITH requested (fund_symbol, fund_name) AS (
    VALUES
        ('161725', '招商中证白酒指数(LOF)A'),
        ('161726', '招商国证生物医药指数(LOF)A'),
        ('001632', '天弘中证食品饮料ETF联接C'),
        ('011103', '天弘中证光伏产业指数C'),
        ('012733', '易方达中证人工智能主题ETF联接A')
)
INSERT INTO reference.instrument_aliases (
    instrument_id, alias_type, alias_value, market, effective_from
)
SELECT instrument.id, 'TICKER', r.fund_symbol, 'FUND_CN', CURRENT_DATE
  FROM requested r
  JOIN reference.instruments instrument
    ON instrument.canonical_name = r.fund_symbol || ' ' || r.fund_name
 WHERE NOT EXISTS (
           SELECT 1 FROM reference.instrument_aliases alias
            WHERE alias.alias_type = 'TICKER'
              AND alias.alias_value = r.fund_symbol
              AND alias.effective_from <= CURRENT_DATE
              AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
       )
ON CONFLICT DO NOTHING;

-- 5) 回填 lookthrough（读 alias 表，可见步骤 1-4 的全部写入）
WITH requested (fund_symbol, holding_symbol, weight_pct) AS (
    VALUES
        ('161725', '600519', '55.00'),
        ('161725', '000858', '28.00'),
        ('161725', '000568', '12.00'),
        ('161725', '600809', '3.00'),
        ('161725', '000596', '2.00'),
        ('161726', '300760', '12.00'),
        ('161726', '603259', '11.00'),
        ('161726', '600276', '10.00'),
        ('161726', '300015', '7.00'),
        ('161726', '300122', '6.00'),
        ('001632', '600519', '25.00'),
        ('001632', '000858', '14.00'),
        ('001632', '603288', '8.00'),
        ('001632', '600887', '8.00'),
        ('001632', '000568', '7.00'),
        ('011103', '601012', '14.00'),
        ('011103', '600438', '11.00'),
        ('011103', '002129', '9.00'),
        ('011103', '300274', '9.00'),
        ('011103', '002459', '7.00'),
        ('012733', '002230', '11.00'),
        ('012733', '002415', '10.00'),
        ('012733', '603019', '7.00'),
        ('012733', '688111', '5.00'),
        ('012733', '000938', '4.00')
),
funds AS (
    SELECT DISTINCT r.fund_symbol, instrument.id AS fund_instrument_id
      FROM requested r
      JOIN reference.instrument_aliases alias
        ON alias.alias_type = 'TICKER' AND alias.alias_value = r.fund_symbol
       AND alias.effective_from <= CURRENT_DATE
       AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
      JOIN reference.instruments instrument ON instrument.id = alias.instrument_id
),
holdings AS (
    SELECT DISTINCT r.holding_symbol, instrument.id AS holding_instrument_id
      FROM requested r
      JOIN reference.instrument_aliases alias
        ON alias.alias_type = 'TICKER' AND alias.alias_value = r.holding_symbol
       AND alias.effective_from <= CURRENT_DATE
       AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
      JOIN reference.instruments instrument ON instrument.id = alias.instrument_id
),
seeded AS (
    INSERT INTO market.fund_lookthrough (
        fund_instrument_id, holding_instrument_id, weight_pct,
        report_period, metadata
    )
    SELECT f.fund_instrument_id, h.holding_instrument_id,
           r.weight_pct::numeric, DATE '2026-06-30',
           jsonb_build_object(
               'source', 'index-disclosure',
               'warning', '基于公开指数机制的估值近似，真季报披露后请用 scripts/ingest_fund_holdings.py 覆盖'
           )
      FROM requested r
      JOIN funds f ON f.fund_symbol = r.fund_symbol
      JOIN holdings h ON h.holding_symbol = r.holding_symbol
    ON CONFLICT (fund_instrument_id, holding_instrument_id, report_period)
    DO NOTHING
    RETURNING 1
)
SELECT count(*) AS lookthrough_rows FROM seeded;

COMMIT;