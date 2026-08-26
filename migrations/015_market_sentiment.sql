-- ==============================================================================
-- WealthSail Migration 015: Market sentiment snapshot
--
-- Daily market-wide sentiment: advance/decline counts, limit-up/down counts,
-- total turnover and (optional) northbound flow, plus a derived 0-100 score.
-- Written by the Worker (or the API on demand) from the AkShare whole-market
-- snapshot; read by the dashboard sentiment card.
-- ==============================================================================

CREATE TABLE IF NOT EXISTS market.market_sentiment (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_date DATE NOT NULL UNIQUE,
    total_up INT,
    total_down INT,
    total_flat INT,
    limit_up INT,
    limit_down INT,
    turnover NUMERIC(20, 2),          -- 成交额（亿元）
    north_flow NUMERIC(20, 2),        -- 北向资金净流入（亿元，可选；2024-08 后实时披露停止，通常为 NULL）
    sentiment_score NUMERIC(5, 2),    -- 综合情绪评分（0-100）
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE market.market_sentiment IS
    '每日全市场情绪快照（涨跌家数/涨停跌停/成交额/北向净流入/情绪评分 0-100）';

-- 权限：浏览器角色不直连；API 可读（看板），Worker 可读写（刷新落库）。
REVOKE ALL ON market.market_sentiment FROM PUBLIC, authenticated;
GRANT SELECT ON market.market_sentiment TO wealthsail_api;
GRANT SELECT, INSERT, UPDATE ON market.market_sentiment TO wealthsail_worker;
