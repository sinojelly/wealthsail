-- =============================================================================
-- WealthSail Migration 041: daily shared market briefs for no-holdings users
--
-- Tenant-neutral public-market context is generated at most once per trade
-- date and reused by every no-holdings report. The cache is append-only and is
-- never exposed to the browser database role.
-- =============================================================================

CREATE TABLE reporting.shared_market_briefs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_date DATE NOT NULL UNIQUE,
    draft JSONB NOT NULL CHECK (jsonb_typeof(draft) = 'object'),
    source_context JSONB NOT NULL CHECK (jsonb_typeof(source_context) = 'object'),
    context_hash CHAR(64) NOT NULL CHECK (context_hash ~ '^[0-9a-f]{64}$'),
    provider TEXT NOT NULL CHECK (length(btrim(provider)) > 0),
    model TEXT NOT NULL CHECK (length(btrim(model)) > 0),
    generation_status TEXT NOT NULL CHECK (
        generation_status IN ('COMPLETED', 'ABSTAINED')
    ),
    token_usage JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(token_usage) = 'object'),
    cost_cny NUMERIC(12, 4) NOT NULL DEFAULT 0 CHECK (cost_cny >= 0),
    quality_status TEXT NOT NULL CHECK (
        quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'UNAVAILABLE')
    ),
    generated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE FUNCTION reporting.protect_shared_market_brief()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = reporting, pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'shared market briefs are append-only';
END;
$$;

CREATE TRIGGER protect_shared_market_briefs_trigger
BEFORE UPDATE OR DELETE ON reporting.shared_market_briefs
FOR EACH ROW EXECUTE FUNCTION reporting.protect_shared_market_brief();

REVOKE ALL ON reporting.shared_market_briefs FROM PUBLIC, authenticated;
GRANT SELECT, INSERT ON reporting.shared_market_briefs TO wealthsail_api, wealthsail_worker;

COMMENT ON TABLE reporting.shared_market_briefs IS
    'One auditable tenant-neutral no-holdings market brief per trade date.';
