-- =============================================================================
-- WealthSail Migration 036: align fund NAV provider source types
-- =============================================================================
-- reference.data_sources.source_type describes the kind of financial data.
-- PUBLIC_API is the license model and is not a valid source_type.  Historical
-- NAV backfill exposed a code path that confused these two fields.  Normalize
-- rows that may already have been registered by the general market refresher;
-- the existing source_type constraint already accepts FUND_NAV and must not be
-- widened for this repair.

UPDATE reference.data_sources
   SET source_type = 'FUND_NAV'
 WHERE name IN ('TiantianFund-EastMoney', 'WealthSail-Fund-Estimator')
   AND source_type <> 'FUND_NAV';
