-- 王芳租户数据补全（tenant 00000000-0000-0000-0000-000000000003）
-- 账本模式对齐张明种子（migration 010）：
--   010 种子已建本金事件 INIT_CASH_IN_WF_001（200,000，append-only 不可改）
--   → 本脚本追加 CASH_IN 300,000（"补充本金"），本金合计 500,000；
--   3 笔 BUY（现金流出 450,000）→ 现金余额 = 50,000；
--   投影快照以成本价锚定（upsert），随后 refresh_market 用真实行情重算市值。
-- 幂等：全部走 DO 块条件插入，可重复执行。
-- 执行角色：lenovo（超级用户，绕过 RLS，与 010 种子一致）。

BEGIN;

-- 1. 平安银行标的（CN_STOCK 000001）
INSERT INTO reference.instruments
    (id, asset_type, primary_market, trading_currency, canonical_name, lifecycle_status)
VALUES
    ('20000000-0000-0000-0000-000000000004', 'CN_STOCK', 'SSE', 'CNY', '平安银行', 'ACTIVE')
ON CONFLICT (id) DO NOTHING;

INSERT INTO reference.instrument_aliases
    (instrument_id, alias_type, alias_value, market, effective_from)
VALUES
    ('20000000-0000-0000-0000-000000000004', 'TICKER', '000001', 'SSE', '2026-01-01')
ON CONFLICT (alias_type, alias_value, market, effective_from) DO NOTHING;

-- 2. 账本事实（幂等：事件不存在才插入事件与腿）
DO $$
BEGIN
    -- 本金口径：目标 500,000。
    -- 分支 A（全新环境）：010 种子 200,000 + 本脚本补充 CASH_IN 300,000；
    -- 分支 B（本脚本曾以 500,000 入金的环境）：追加 CASH_OUT 200,000 冲正。
    IF NOT EXISTS (SELECT 1 FROM ledger.ledger_events
                   WHERE id = '40000000-0000-0000-0000-000000000101') THEN
        INSERT INTO ledger.ledger_events
            (id, tenant_id, account_id, event_type, status, trade_date, occurred_at,
             idempotency_key, description, schema_version)
        VALUES
            ('40000000-0000-0000-0000-000000000101', '00000000-0000-0000-0000-000000000003',
             '30000000-0000-0000-0000-000000000004', 'CASH_IN', 'CONFIRMED', CURRENT_DATE, now(),
             'wangfang-seed-topup-20260824', '补充本金至 500,000', 1);
        INSERT INTO ledger.ledger_legs
            (tenant_id, event_id, leg_type, instrument_id, quantity, amount, unit_price)
        VALUES
            ('00000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000101',
             'CASH', NULL, NULL, 300000.00, NULL);
    ELSIF NOT EXISTS (SELECT 1 FROM ledger.ledger_events
                      WHERE id = '40000000-0000-0000-0000-000000000105') THEN
        INSERT INTO ledger.ledger_events
            (id, tenant_id, account_id, event_type, status, trade_date, occurred_at,
             idempotency_key, description, schema_version)
        VALUES
            ('40000000-0000-0000-0000-000000000105', '00000000-0000-0000-0000-000000000003',
             '30000000-0000-0000-0000-000000000004', 'CASH_OUT', 'CONFIRMED', CURRENT_DATE, now(),
             'wangfang-seed-reverse-topup-20260824', '冲正超额入金，本金修正为 500,000', 1);
        INSERT INTO ledger.ledger_legs
            (tenant_id, event_id, leg_type, instrument_id, quantity, amount, unit_price)
        VALUES
            ('00000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000105',
             'CASH', NULL, NULL, -200000.00, NULL);
    END IF;

    -- 三笔买入
    IF NOT EXISTS (SELECT 1 FROM ledger.ledger_events
                   WHERE id = '40000000-0000-0000-0000-000000000102') THEN
        INSERT INTO ledger.ledger_events
            (id, tenant_id, account_id, event_type, status, trade_date, occurred_at,
             idempotency_key, description, schema_version)
        VALUES
            ('40000000-0000-0000-0000-000000000102', '00000000-0000-0000-0000-000000000003',
             '30000000-0000-0000-0000-000000000004', 'BUY', 'CONFIRMED', CURRENT_DATE, now(),
             'wangfang-seed-buy-pab-20260824', '期初买入 平安银行 1000 股 @11.00', 1),
            ('40000000-0000-0000-0000-000000000103', '00000000-0000-0000-0000-000000000003',
             '30000000-0000-0000-0000-000000000004', 'BUY', 'CONFIRMED', CURRENT_DATE, now(),
             'wangfang-seed-buy-moutai-20260824', '期初买入 贵州茅台 200 股 @2080.00', 1),
            ('40000000-0000-0000-0000-000000000104', '00000000-0000-0000-0000-000000000003',
             '30000000-0000-0000-0000-000000000004', 'BUY', 'CONFIRMED', CURRENT_DATE, now(),
             'wangfang-seed-buy-etf-20260824', '期初买入 沪深300ETF 5000 份 @4.60', 1);
        INSERT INTO ledger.ledger_legs
            (tenant_id, event_id, leg_type, instrument_id, quantity, amount, unit_price)
        VALUES
            ('00000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000102',
             'INSTRUMENT', '20000000-0000-0000-0000-000000000004', 1000, NULL, 11.00),
            ('00000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000102',
             'CASH', NULL, NULL, -11000.00, NULL),
            ('00000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000103',
             'INSTRUMENT', '20000000-0000-0000-0000-000000000001', 200, NULL, 2080.00),
            ('00000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000103',
             'CASH', NULL, NULL, -416000.00, NULL),
            ('00000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000104',
             'INSTRUMENT', '20000000-0000-0000-0000-000000000002', 5000, NULL, 4.60),
            ('00000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000104',
             'CASH', NULL, NULL, -23000.00, NULL);
    END IF;
END $$;

-- 3. 持仓投影锚点（upsert，as_of=今日；refresh_market 将以真实行情覆盖市值）
INSERT INTO ledger.position_snapshots
    (tenant_id, account_id, instrument_id, as_of, quantity, avg_cost,
     market_price, market_value, unrealized_pnl, data_quality_status)
VALUES
    ('00000000-0000-0000-0000-000000000003', '30000000-0000-0000-0000-000000000004',
     '20000000-0000-0000-0000-000000000004', CURRENT_DATE, 1000, 11.00,
     11.00, 11000.00, 0.00, 'STALE'),
    ('00000000-0000-0000-0000-000000000003', '30000000-0000-0000-0000-000000000004',
     '20000000-0000-0000-0000-000000000001', CURRENT_DATE, 200, 2080.00,
     2080.00, 416000.00, 0.00, 'STALE'),
    ('00000000-0000-0000-0000-000000000003', '30000000-0000-0000-0000-000000000004',
     '20000000-0000-0000-0000-000000000002', CURRENT_DATE, 5000, 4.60,
     4.60, 23000.00, 0.00, 'STALE')
ON CONFLICT (tenant_id, account_id, instrument_id, as_of) DO UPDATE
SET quantity = EXCLUDED.quantity,
    avg_cost = EXCLUDED.avg_cost,
    market_price = EXCLUDED.market_price,
    market_value = EXCLUDED.market_value,
    unrealized_pnl = EXCLUDED.unrealized_pnl,
    data_quality_status = EXCLUDED.data_quality_status;

COMMIT;
