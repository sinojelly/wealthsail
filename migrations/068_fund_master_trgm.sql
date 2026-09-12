BEGIN;

-- 068: Fuzzy name matching for the fund-screener master list.  AkShare's
-- fund_name_em stores abbreviated official names (e.g. "兴全合宜混合(LOF)A")
-- while users paste the full marketing name ("兴全合宜灵活配置混合(LOF)A"),
-- so exact ILIKE misses.  pg_trgm similarity catches those variants.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_fund_master_name_trgm
    ON reference.fund_master USING gin (fund_name gin_trgm_ops);

COMMIT;
