-- =============================================================================
-- WealthSail Migration 027: Worker-owned market-hot read model
--
-- Market-hot queries must never fan out to external Providers from an API
-- request.  The Worker records immutable, provenance-bearing snapshots here;
-- the API role receives read-only access and browser roles receive none.
-- =============================================================================

CREATE TABLE market.market_hot_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    items JSONB NOT NULL,
    item_count INTEGER NOT NULL,
    source_name TEXT NOT NULL,
    source_as_of TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ NOT NULL,
    quality_status TEXT NOT NULL,
    method_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT market_hot_items_array CHECK (jsonb_typeof(items) = 'array'),
    CONSTRAINT market_hot_item_count_valid CHECK (
        item_count >= 0 AND item_count = jsonb_array_length(items)
    ),
    CONSTRAINT market_hot_quality_check CHECK (
        quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'UNAVAILABLE')
    )
);

CREATE INDEX idx_market_hot_latest
    ON market.market_hot_snapshots (retrieved_at DESC, id DESC);

COMMENT ON TABLE market.market_hot_snapshots IS
    'Worker-owned immutable market-hot snapshots; API reads only and never calls Providers.';

REVOKE ALL ON market.market_hot_snapshots FROM PUBLIC, authenticated;
GRANT SELECT ON market.market_hot_snapshots TO wealthsail_api;
GRANT SELECT, INSERT ON market.market_hot_snapshots TO wealthsail_worker;
