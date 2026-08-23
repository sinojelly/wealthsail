-- ==============================================================================
-- WealthSail Migration 006: Mock Seed Data for 3 Isolated Pilot Tenants
-- Version: v2.0.0
-- Description: 插入 3 个独立封闭试点租户的完整演示标的、双式账本、持仓与四时段报告
-- ==============================================================================

-- 1. 注册基础数据源
INSERT INTO reference.data_sources (id, name, source_type, license_model, allowed_usage, priority, status)
VALUES 
    ('10000000-0000-0000-0000-000000000001', 'AkShare-OpenSource', 'MARKET_DATA', 'ACADEMIC_FREE', 'RESEARCH_ONLY', 100, 'ACTIVE'),
    ('10000000-0000-0000-0000-000000000002', 'SGE-GoldBenchmark', 'MARKET_DATA', 'PUBLIC_DATA', 'PRICE_OBSERVATION', 90, 'ACTIVE'),
    ('10000000-0000-0000-0000-000000000003', 'DeepSeek-V3-LLM', 'LLM', 'COMMERCIAL_API', 'SYNTHESIS_AND_DRAFT', 10, 'ACTIVE')
ON CONFLICT (name) DO NOTHING;

-- 2. 注册基础标的主数据
INSERT INTO reference.instruments (id, asset_type, primary_market, trading_currency, canonical_name, lifecycle_status)
VALUES
    ('20000000-0000-0000-0000-000000000001', 'CN_STOCK', 'SSE', 'CNY', '贵州茅台', 'ACTIVE'),
    ('20000000-0000-0000-0000-000000000002', 'EXCHANGE_FUND', 'SSE', 'CNY', '华泰柏瑞沪深300ETF', 'ACTIVE'),
    ('20000000-0000-0000-0000-000000000003', 'OFF_EXCHANGE_FUND', 'FUND_CN', 'CNY', '易方达优质精选混合', 'ACTIVE')
ON CONFLICT (id) DO NOTHING;

-- 标的代码别名
INSERT INTO reference.instrument_aliases (instrument_id, alias_type, alias_value, market)
VALUES
    ('20000000-0000-0000-0000-000000000001', 'TICKER', '600519', 'SSE'),
    ('20000000-0000-0000-0000-000000000002', 'TICKER', '510300', 'SSE'),
    ('20000000-0000-0000-0000-000000000003', 'TICKER', '110011', 'FUND_CN')
ON CONFLICT DO NOTHING;

-- 3. 创建 3 个封闭试点租户与用户
INSERT INTO identity.tenants (id, name, status, base_currency)
VALUES
    ('00000000-0000-0000-0000-000000000001', '张明 (稳健全天候)', 'ACTIVE', 'CNY'),
    ('00000000-0000-0000-0000-000000000002', '李华 (科技高弹性)', 'ACTIVE', 'CNY'),
    ('00000000-0000-0000-0000-000000000003', '王芳 (红利固收防守)', 'ACTIVE', 'CNY')
ON CONFLICT (id) DO NOTHING;

INSERT INTO identity.profiles (id, email, nickname)
VALUES
    ('00000000-0000-0000-0000-000000000001', 'zhangming@example.com', '张明'),
    ('00000000-0000-0000-0000-000000000002', 'lihua@example.com', '李华'),
    ('00000000-0000-0000-0000-000000000003', 'wangfang@example.com', '王芳')
ON CONFLICT (id) DO NOTHING;

INSERT INTO identity.tenant_members (tenant_id, user_id, role, status)
VALUES
    ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'OWNER', 'ACTIVE'),
    ('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000002', 'OWNER', 'ACTIVE'),
    ('00000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000003', 'OWNER', 'ACTIVE')
ON CONFLICT DO NOTHING;

-- 4. 为租户 1 (张明) 创建账户与初始不可变账本
INSERT INTO identity.accounts (id, tenant_id, account_type, name, base_currency)
VALUES
    ('30000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'BROKERAGE', '华泰主证券账户', 'CNY'),
    ('30000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'FUND_PLATFORM', '天天基金账户', 'CNY')
ON CONFLICT DO NOTHING;

-- 插入入金与买入账本事件 (Event 1: 入金 500,000 元)
INSERT INTO ledger.ledger_events (id, tenant_id, account_id, event_type, status, trade_date, idempotency_key, description)
VALUES (
    '40000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    'CASH_IN', 'CONFIRMED', CURRENT_DATE, 'INIT_CASH_IN_ZM_001', '初始总本金入账'
) ON CONFLICT (tenant_id, idempotency_key) DO NOTHING;

INSERT INTO ledger.ledger_legs (tenant_id, event_id, leg_type, currency, amount)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'CASH', 'CNY', 500000.00
) ON CONFLICT DO NOTHING;

-- 插入买入事件 (Event 2: 买入 300 股贵州茅台，成交均价 1400.00，花费 420000 现金)
INSERT INTO ledger.ledger_events (id, tenant_id, account_id, event_type, status, trade_date, idempotency_key, description)
VALUES (
    '40000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    'BUY', 'CONFIRMED', CURRENT_DATE, 'BUY_600519_ZM_001', '建仓贵州茅台 300股'
) ON CONFLICT (tenant_id, idempotency_key) DO NOTHING;

INSERT INTO ledger.ledger_legs (tenant_id, event_id, leg_type, instrument_id, quantity, unit_price)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000002',
    'INSTRUMENT', '20000000-0000-0000-0000-000000000001', 300.0000, 1400.00
) ON CONFLICT DO NOTHING;

INSERT INTO ledger.ledger_legs (tenant_id, event_id, leg_type, currency, amount)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000002',
    'CASH', 'CNY', -420000.00
) ON CONFLICT DO NOTHING;

-- 5. 初始化持仓快照与净值投影 (可用现金 80,000 元，茅台市值 426,000 元，总净值 506,000 元)
INSERT INTO ledger.position_snapshots (tenant_id, account_id, instrument_id, as_of, quantity, avg_cost, market_price, market_value, unrealized_pnl, data_quality_status)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    CURRENT_DATE, 300.0000, 1400.0000, 1420.0000, 426000.00, 6000.00, 'FRESH'
) ON CONFLICT (tenant_id, account_id, instrument_id, as_of) DO UPDATE SET quantity = EXCLUDED.quantity;

INSERT INTO ledger.account_nav_snapshots (tenant_id, account_id, as_of, cash_balance, positions_market_value, total_nav, daily_pnl, cumulative_pnl, data_quality_status)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    CURRENT_DATE, 80000.00, 426000.00, 506000.00, 6000.00, 6000.00, 'FRESH'
) ON CONFLICT (tenant_id, account_id, as_of) DO UPDATE SET total_nav = EXCLUDED.total_nav;

-- 6. 插入投资论点 (Thesis)
INSERT INTO research.theses (id, tenant_id, account_id, instrument_id, summary, horizon, invalidation_conditions, target_price)
VALUES (
    '50000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    '高端白酒商业壁垒深厚，估值处于历史 15% 分位，分红收益率提供安全垫。',
    'LONG_TERM',
    '["单季度批价连续跌破 2000 元", "日线收盘破位 1350 元且 3 日内无法收回"]'::jsonb,
    1650.00
) ON CONFLICT DO NOTHING;
