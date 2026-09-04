BEGIN;

-- 053 的 INSERT 通过 alias JOIN 解析持仓股票；未注册过的股票 JOIN 不到
-- 就被静默跳过，导致 053 applied 但 fund_lookthrough 仍为 0 行。
-- 本迁移补齐链路：
--   1) 自动注册缺失的持仓股票（CN_STOCK + TICKER alias，market 按代码前缀）
--   2) 基金若因任何原因缺 alias 也补注册（OFF_EXCHANGE_FUND/FUND_CN）
--   3) 重新 INSERT lookthrough（ON CONFLICT DO NOTHING，与 053 幂等兼容）
-- 权重仍是基于公开指数机制的估算近似，metadata.warning 保留该标记。

WITH requested (
    fund_symbol, fund_name, holding_symbol, holding_name, weight_pct
) AS (
    VALUES
        ('161725', '招商国证生物医药指数(LOF)A', '600519', '贵州茅台', '55.00'),
        ('161725', '招商国证生物医药指数(LOF)A', '000858', '五粮液', '28.00'),
        ('161725', '招商国证生物医药指数(LOF)A', '000568', '泸州老窖', '12.00'),
        ('161725', '招商国证生物医药指数(LOF)A', '600809', '山西汾酒', '3.00'),
        ('161725', '招商国证生物医药指数(LOF)A', '000596', '古井贡酒', '2.00'),
        ('161726', '招商国证生物医药指数(LOF)A', '300760', '迈瑞医疗', '12.00'),
        ('161726', '招商国证生物医药指数(LOF)A', '603259', '药明康德', '11.00'),
        ('161726', '招商国证生物医药指数(LOF)A', '600276', '恒瑞医药', '10.00'),
        ('161726', '招商国证生物医药指数(LOF)A', '300015', '爱尔眼科', '7.00'),
        ('161726', '招商国证生物医药指数(LOF)A', '300122', '智飞生物', '6.00'),
        ('001632', '天弘中证食品饮料ETF联接C', '600519', '贵州茅台', '25.00'),
        ('001632', '天弘中证食品饮料ETF联接C', '000858', '五粮液', '14.00'),
        ('001632', '天弘中证食品饮料ETF联接C', '603288', '海天味业', '8.00'),
        ('001632', '天弘中证食品饮料ETF联接C', '600887', '伊利股份', '8.00'),
        ('001632', '天弘中证食品饮料ETF联接C', '000568', '泸州老窖', '7.00'),
        ('011103', '天弘中证光伏产业指数C', '601012', '隆基绿能', '14.00'),
        ('011103', '天弘中证光伏产业指数C', '600438', '通威股份', '11.00'),
        ('011103', '天弘中证光伏产业指数C', '002129', 'TCL中环', '9.00'),
        ('011103', '天弘中证光伏产业指数C', '300274', '阳光电源', '9.00'),
        ('011103', '天弘中证光伏产业指数C', '002459', '晶澳科技', '7.00'),
        ('012733', '易方达中证人工智能主题ETF联接A', '002230', '科大讯飞', '11.00'),
        ('012733', '易方达中证人工智能主题ETF联接A', '002415', '海康威视', '10.00'),
        ('012733', '易方达中证人工智能主题ETF联接A', '603019', '中科曙光', '7.00'),
        ('012733', '易方达中证人工智能主题ETF联接A', '688111', '金山办公', '5.00'),
        ('012733', '易方达中证人工智能主题ETF联接A', '000938', '紫光股份', '4.00')
),
-- 按代码前缀推断上市交易所（60/688 → SSE，其余深市 → SZSE）
typed_holdings AS (
    SELECT DISTINCT r.holding_symbol, r.holding_name,
           CASE WHEN r.holding_symbol LIKE '6%' THEN 'SSE' ELSE 'SZSE' END AS market
      FROM requested r
),
-- 注册缺失的持仓股票（幂等：已有 TICKER alias 的跳过）
missing_holdings AS (
    SELECT th.holding_symbol, th.holding_name, th.market
      FROM typed_holdings th
     WHERE NOT EXISTS (
               SELECT 1
                 FROM reference.instrument_aliases ha
                WHERE ha.alias_type = 'TICKER'
                  AND ha.alias_value = th.holding_symbol
                  AND ha.effective_from <= CURRENT_DATE
                  AND (ha.effective_to IS NULL OR ha.effective_to >= CURRENT_DATE)
           )
),
inserted_holdings AS (
    INSERT INTO reference.instruments (
        asset_type, primary_market, trading_currency, canonical_name,
        lifecycle_status, metadata
    )
    SELECT 'CN_STOCK', mh.market, 'CNY', mh.holding_name, 'ACTIVE',
           jsonb_build_object(
               'registeredBy', 'fund-holdings-seed',
               'source', 'index-fund-holdings'
           )
      FROM missing_holdings mh
    RETURNING id, canonical_name
),
inserted_holding_aliases AS (
    INSERT INTO reference.instrument_aliases (
        instrument_id, alias_type, alias_value, market, effective_from
    )
    SELECT ih.id, 'TICKER', mh.holding_symbol, mh.market, CURRENT_DATE
      FROM inserted_holdings ih
      JOIN missing_holdings mh ON mh.holding_name = ih.canonical_name
    ON CONFLICT DO NOTHING
    RETURNING 1
),
-- 注册缺失的基金（正常情况下 052 已建，这里兜底）
missing_funds AS (
    SELECT DISTINCT r.fund_symbol, r.fund_name
      FROM requested r
     WHERE NOT EXISTS (
               SELECT 1
                 FROM reference.instrument_aliases fa
                WHERE fa.alias_type = 'TICKER'
                  AND fa.alias_value = r.fund_symbol
                  AND fa.effective_from <= CURRENT_DATE
                  AND (fa.effective_to IS NULL OR fa.effective_to >= CURRENT_DATE)
           )
),
inserted_funds AS (
    INSERT INTO reference.instruments (
        asset_type, primary_market, trading_currency, canonical_name,
        lifecycle_status, metadata
    )
    SELECT 'OFF_EXCHANGE_FUND', 'FUND_CN', 'CNY',
           mf.fund_symbol || ' ' || mf.fund_name, 'ACTIVE',
           jsonb_build_object(
               'registeredBy', 'fund-holdings-seed',
               'source', 'index-fund-holdings'
           )
      FROM missing_funds mf
    RETURNING id, canonical_name
),
inserted_fund_aliases AS (
    INSERT INTO reference.instrument_aliases (
        instrument_id, alias_type, alias_value, market, effective_from
    )
    SELECT ifn.id, 'TICKER', mf.fund_symbol, 'FUND_CN', CURRENT_DATE
      FROM inserted_funds ifn
      JOIN missing_funds mf
        ON ifn.canonical_name = mf.fund_symbol || ' ' || mf.fund_name
    ON CONFLICT DO NOTHING
    RETURNING 1
),
funds AS (
    SELECT DISTINCT r.fund_symbol, fi.id AS fund_instrument_id
      FROM requested r
      JOIN reference.instrument_aliases fa
        ON fa.alias_type = 'TICKER' AND fa.alias_value = r.fund_symbol
       AND fa.effective_from <= CURRENT_DATE
       AND (fa.effective_to IS NULL OR fa.effective_to >= CURRENT_DATE)
      JOIN reference.instruments fi ON fi.id = fa.instrument_id
),
holdings AS (
    SELECT DISTINCT r.holding_symbol, hi.id AS holding_instrument_id
      FROM requested r
      JOIN reference.instrument_aliases ha
        ON ha.alias_type = 'TICKER' AND ha.alias_value = r.holding_symbol
       AND ha.effective_from <= CURRENT_DATE
       AND (ha.effective_to IS NULL OR ha.effective_to >= CURRENT_DATE)
      JOIN reference.instruments hi ON hi.id = ha.instrument_id
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
SELECT (SELECT count(*) FROM inserted_holdings) AS holdings_registered,
       (SELECT count(*) FROM inserted_funds) AS funds_registered,
       (SELECT count(*) FROM seeded) AS lookthrough_rows;

COMMIT;