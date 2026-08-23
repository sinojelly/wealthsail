-- ==============================================================================
-- WealthSail Migration 003: Reference Master Data & Immutable Ledger
-- Version: v2.0.0
-- Description: 标的主数据、交易日历、数据源及不可变双式记账体系
-- ==============================================================================

-- 1. 标的主数据表 (Instruments)
CREATE TABLE IF NOT EXISTS reference.instruments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_type TEXT NOT NULL CHECK (asset_type IN ('CN_STOCK', 'EXCHANGE_FUND', 'OFF_EXCHANGE_FUND', 'BOND', 'GOLD_SPOT')),
    primary_market TEXT NOT NULL CHECK (primary_market IN ('SSE', 'SZSE', 'BSE', 'FUND_CN', 'SGE')),
    trading_currency CHAR(3) NOT NULL DEFAULT 'CNY',
    canonical_name TEXT NOT NULL,
    isin TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (lifecycle_status IN ('ACTIVE', 'SUSPENDED', 'DELISTED', 'LIQUIDATED', 'MERGED')),
    inception_date DATE,
    end_date DATE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. 标的代码别名映射表 (Instrument Aliases - 支持多市场与代码变更)
CREATE TABLE IF NOT EXISTS reference.instrument_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id) ON DELETE CASCADE,
    alias_type TEXT NOT NULL CHECK (alias_type IN ('TICKER', 'NAME_SHORT', 'PINYIN', 'PROVIDER_SYMBOL')),
    alias_value TEXT NOT NULL,
    market TEXT NOT NULL,
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (alias_type, alias_value, market, effective_from)
);

-- 3. 交易日历表 (Trading Calendars)
CREATE TABLE IF NOT EXISTS reference.trading_calendars (
    market TEXT NOT NULL,
    calendar_date DATE NOT NULL,
    is_trading_day BOOLEAN NOT NULL DEFAULT TRUE,
    is_half_day BOOLEAN NOT NULL DEFAULT FALSE,
    fund_cutoff_time TIME DEFAULT '15:00:00',
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    PRIMARY KEY (market, calendar_date)
);

-- 4. 数据源注册表 (Data Sources)
CREATE TABLE IF NOT EXISTS reference.data_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL CHECK (source_type IN ('MARKET_DATA', 'FUND_NAV', 'NEWS', 'MACRO', 'LLM')),
    license_model TEXT NOT NULL,
    allowed_usage TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RESTRICTED', 'DEPRECATED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. 不可变经济事件事实表 (Ledger Events - 唯一经济事实源)
CREATE TABLE IF NOT EXISTS ledger.ledger_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES identity.accounts(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'CASH_IN', 'CASH_OUT', 'BUY', 'SELL', 'DIVIDEND_CASH',
        'DIVIDEND_REINVEST', 'FEE', 'TAX', 'SPLIT', 'MERGER',
        'TRANSFER_IN', 'TRANSFER_OUT', 'ADJUSTMENT', 'REVERSAL'
    )),
    status TEXT NOT NULL DEFAULT 'CONFIRMED' CHECK (status IN ('PENDING', 'CONFIRMED', 'REVERSED')),
    trade_date DATE NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_id UUID REFERENCES reference.data_sources(id),
    external_event_id TEXT,
    idempotency_key TEXT NOT NULL,
    reverses_event_id UUID REFERENCES ledger.ledger_events(id),
    description TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

-- 6. 双式记账交易腿明细表 (Ledger Legs - 借贷守恒)
CREATE TABLE IF NOT EXISTS ledger.ledger_legs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES ledger.ledger_events(id) ON DELETE RESTRICT,
    leg_type TEXT NOT NULL CHECK (leg_type IN ('CASH', 'INSTRUMENT', 'FEE', 'TAX')),
    instrument_id UUID REFERENCES reference.instruments(id),
    currency CHAR(3) DEFAULT 'CNY',
    quantity NUMERIC(28, 10), -- 证券数量/基金份额/黄金克数 (正为买入入库，负为卖出扣减)
    amount NUMERIC(28, 8),   -- 现金变动 (正为现金增加，负为现金扣减)
    unit_price NUMERIC(28, 10), -- 成交单价/净值/金价
    lot_ref UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (leg_type = 'INSTRUMENT' AND instrument_id IS NOT NULL AND quantity IS NOT NULL)
        OR
        (leg_type <> 'INSTRUMENT' AND currency IS NOT NULL AND amount IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_ledger_legs_event ON ledger.ledger_legs(tenant_id, event_id);

-- 7. 开放持仓批次表 (Position Lots - 派生只读投影)
CREATE TABLE IF NOT EXISTS ledger.position_lots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES identity.accounts(id) ON DELETE CASCADE,
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id),
    acquired_at TIMESTAMPTZ NOT NULL,
    initial_quantity NUMERIC(28, 10) NOT NULL,
    quantity_open NUMERIC(28, 10) NOT NULL CHECK (quantity_open >= 0),
    cost_amount NUMERIC(28, 8) NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'CNY',
    projection_version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 8. 每日持仓快照表 (Position Snapshots - 派生只读投影)
CREATE TABLE IF NOT EXISTS ledger.position_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES identity.accounts(id) ON DELETE CASCADE,
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id),
    as_of DATE NOT NULL,
    quantity NUMERIC(28, 10) NOT NULL,
    avg_cost NUMERIC(28, 10) NOT NULL,
    market_price NUMERIC(28, 10),
    market_value NUMERIC(28, 8),
    unrealized_pnl NUMERIC(28, 8),
    data_quality_status TEXT NOT NULL DEFAULT 'FRESH' CHECK (data_quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'UNAVAILABLE')),
    source_ledger_cursor BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account_id, instrument_id, as_of)
);

-- 9. 账户净资产快照表 (Account NAV Snapshots - 派生只读投影)
CREATE TABLE IF NOT EXISTS ledger.account_nav_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES identity.accounts(id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    cash_balance NUMERIC(28, 8) NOT NULL,
    positions_market_value NUMERIC(28, 8) NOT NULL,
    total_nav NUMERIC(28, 8) NOT NULL,
    daily_pnl NUMERIC(28, 8) DEFAULT 0.00,
    cumulative_pnl NUMERIC(28, 8) DEFAULT 0.00,
    data_quality_status TEXT NOT NULL DEFAULT 'FRESH',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account_id, as_of)
);
