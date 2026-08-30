-- =============================================================================
-- WealthSail Migration 038: Worker projection privileges and confirmation mode
--
-- Migration 037 introduced post-import holding-start decisions.  The follow-up
-- implementation revalues deterministic position/account projections after a
-- NAV task, but the BYPASSRLS Worker role had only read access to those tables.
-- Grant only the statements and update columns used by that replay.
--
-- Also persist whether a holding-start decision changes the effective date.
-- Accepting an unchanged estimate is an auditable acknowledgement and must not
-- create a NAV backfill; changing the date still queues one scoped fund task.
-- =============================================================================

ALTER TABLE ledger.position_holding_start_decisions
    ADD COLUMN previous_started_on DATE,
    ADD COLUMN recalculation_required BOOLEAN NOT NULL DEFAULT TRUE;

GRANT SELECT ON identity.accounts TO wealthsail_worker;

GRANT INSERT, DELETE ON ledger.position_snapshots TO wealthsail_worker;
GRANT UPDATE (
    market_price, market_value, unrealized_pnl, data_quality_status
) ON ledger.position_snapshots TO wealthsail_worker;

GRANT INSERT ON ledger.account_nav_snapshots TO wealthsail_worker;
GRANT UPDATE (
    cash_balance, positions_market_value, total_nav, daily_pnl,
    cumulative_pnl, data_quality_status, valuation_coverage_pct,
    unpriced_position_count, known_value
) ON ledger.account_nav_snapshots TO wealthsail_worker;

COMMENT ON COLUMN ledger.position_holding_start_decisions.previous_started_on IS
    'Effective metric start date observed when this append-only decision was accepted.';
COMMENT ON COLUMN ledger.position_holding_start_decisions.recalculation_required IS
    'True only when started_on differs from previous_started_on and a scoped rebuild is required.';
