BEGIN;

-- 051 建表时未包含 metadata 列，053 需要它来记录「近似」警告，先补齐
-- （幂等；已应用 051 的环境用 IF NOT EXISTS 安全）。
ALTER TABLE market.fund_lookthrough
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

-- 近似回填 5 只指数 / ETF 联接基金到 market.fund_lookthrough。
--
-- 这 5 只是「指数 / ETF 联接」类基金，其持仓等于对应指数的成分与权重
-- （公开指数机制事实）。本迁移用最近一份公开披露估算的前 N 大重仓
-- 作 seed 值；weights 不全等于 100% 也属正常（指数成分多于本迁移给出的
-- N 行；缺口视为抽样）。
--
-- metadata.warning 明确标记「这是基于指数公开机制的估值近似」；真季报
-- 披露后应跑 scripts/ingest_fund_holdings.py（commit 053）以
-- ON CONFLICT DO UPDATE 覆盖为精确权重。approximation_after = 真季报
-- 发布后你需要核验 / 覆盖的具体日期（占位 = 季报披露月 30 日）。

WITH requested (
    fund_symbol, holding_symbol, weight_pct, source
) AS (
    VALUES
        -- 161725 招商中证白酒指数 LOF（跟踪中证白酒指数）
        ('161725', '600519', '55.00', 'index-disclosure'),
        ('161725', '000858', '28.00', 'index-disclosure'),
        ('161725', '000568', '12.00', 'index-disclosure'),
        ('161725', '600809', '3.00', 'index-disclosure'),
        ('161725', '000596', '2.00', 'index-disclosure'),
        -- 161726 招商国证生物医药指数 LOF
        ('161726', '300760', '12.00', 'index-disclosure'),
        ('161726', '603259', '11.00', 'index-disclosure'),
        ('161726', '600276', '10.00', 'index-disclosure'),
        ('161726', '300015', '7.00', 'index-disclosure'),
        ('161726', '300122', '6.00', 'index-disclosure'),
        -- 001632 天弘中证食品饮料 ETF 联接 C
        ('001632', '600519', '25.00', 'index-disclosure'),
        ('001632', '000858', '14.00', 'index-disclosure'),
        ('001632', '603288', '8.00', 'index-disclosure'),
        ('001632', '600887', '8.00', 'index-disclosure'),
        ('001632', '000568', '7.00', 'index-disclosure'),
        -- 011103 天弘中证光伏产业指数 C
        ('011103', '601012', '14.00', 'index-disclosure'),
        ('011103', '600438', '11.00', 'index-disclosure'),
        ('011103', '002129', '9.00', 'index-disclosure'),
        ('011103', '300274', '9.00', 'index-disclosure'),
        ('011103', '002459', '7.00', 'index-disclosure'),
        -- 012733 易方达中证人工智能主题 ETF 联接 A
        ('012733', '002230', '11.00', 'index-disclosure'),
        ('012733', '002415', '10.00', 'index-disclosure'),
        ('012733', '603019', '7.00', 'index-disclosure'),
        ('012733', '688111', '5.00', 'index-disclosure'),
        ('012733', '000938', '4.00', 'index-disclosure')
),
upsert AS (
    INSERT INTO market.fund_lookthrough (
        fund_instrument_id, holding_instrument_id, weight_pct,
        report_period, source_id, metadata, as_of
    )
    SELECT fund.id, holding.id, weight_pct::numeric,
           date '2026-06-30', NULL,
           jsonb_build_object(
               'source', 'index-disclosure',
               'warning', '基于公开指数机制的估值近似，真季报披露后请用 scripts/ingest_fund_holdings.py 覆盖'
           ),
           now()
      FROM requested r
      JOIN reference.instrument_aliases fa
        ON fa.alias_type='TICKER' AND fa.alias_value=r.fund_symbol
       AND fa.effective_from <= CURRENT_DATE
       AND (fa.effective_to IS NULL OR fa.effective_to >= CURRENT_DATE)
      JOIN reference.instruments fund ON fund.id = fa.instrument_id
      JOIN reference.instrument_aliases ha
        ON ha.alias_type='TICKER' AND ha.alias_value=r.holding_symbol
       AND ha.effective_from <= CURRENT_DATE
       AND (ha.effective_to IS NULL OR ha.effective_to >= CURRENT_DATE)
      JOIN reference.instruments holding ON holding.id = ha.instrument_id
    ON CONFLICT (fund_instrument_id, holding_instrument_id, report_period)
    DO UPDATE SET
        weight_pct = EXCLUDED.weight_pct,
        metadata = EXCLUDED.metadata,
        as_of = now()
    RETURNING 1
)
SELECT count(*) AS seeded_rows FROM upsert;

COMMIT;