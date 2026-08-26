-- =============================================================================
-- WealthSail Migration 018: 市场情绪快照的数据治理元数据
--
-- 市场情绪是衍生指标，不是一个脱离来源即可解释的“分数”。每行必须能够回答：
-- 数据从哪里来、源时间是什么、何时抓取、覆盖率多少、质量如何、算法版本为何。
-- API 只读取该表；只有独立 Worker 角色可写入。
-- =============================================================================

ALTER TABLE market.market_sentiment
    ADD COLUMN source_name TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN',
    ADD COLUMN source_as_of TIMESTAMPTZ,
    ADD COLUMN retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN quality_status TEXT NOT NULL DEFAULT 'STALE',
    ADD COLUMN coverage_pct NUMERIC(5, 2),
    ADD COLUMN method_version TEXT NOT NULL DEFAULT 'sentiment/legacy';

ALTER TABLE market.market_sentiment
    ADD CONSTRAINT market_sentiment_counts_nonnegative CHECK (
        COALESCE(total_up, 0) >= 0 AND COALESCE(total_down, 0) >= 0
        AND COALESCE(total_flat, 0) >= 0 AND COALESCE(limit_up, 0) >= 0
        AND COALESCE(limit_down, 0) >= 0
    ),
    ADD CONSTRAINT market_sentiment_score_range CHECK (
        sentiment_score IS NULL OR sentiment_score BETWEEN 0 AND 100
    ),
    ADD CONSTRAINT market_sentiment_coverage_range CHECK (
        coverage_pct IS NULL OR coverage_pct BETWEEN 0 AND 100
    ),
    ADD CONSTRAINT market_sentiment_quality_check CHECK (
        quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'UNAVAILABLE')
    );

UPDATE market.market_sentiment
SET source_as_of = COALESCE(source_as_of, updated_at),
    retrieved_at = COALESCE(retrieved_at, updated_at),
    quality_status = 'STALE'
WHERE source_as_of IS NULL;

COMMENT ON COLUMN market.market_sentiment.coverage_pct IS
    '本次快照中具备有效涨跌幅的标的占源快照总行数百分比；未知时为 NULL。';
COMMENT ON COLUMN market.market_sentiment.method_version IS
    '涨跌家数、各板涨跌停阈值和综合分数的可追溯算法版本。';
