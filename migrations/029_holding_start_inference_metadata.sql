-- =============================================================================
-- WealthSail Migration 029: auditable holding-start inference metadata
--
-- A candidate date is not meaningful without its derivation method and
-- confidence. Persist both on the import item so a restored DRAFT shows the
-- same recommendation, and copy the effective confidence into the daily
-- period projection. Aggregate portfolio orders remain estimates; they never
-- become synthetic per-fund trades.
-- =============================================================================

ALTER TABLE ledger.portfolio_import_items
    ADD COLUMN holding_start_method TEXT NOT NULL DEFAULT 'INSUFFICIENT_DATA',
    ADD COLUMN holding_start_confidence TEXT NOT NULL DEFAULT 'NONE',
    ADD COLUMN holding_start_reasons TEXT[] NOT NULL DEFAULT '{}'::text[];

UPDATE ledger.portfolio_import_items
   SET holding_start_method = CASE holding_start_status
           WHEN 'RECONCILED' THEN 'TRANSACTION_RECONCILED'
           WHEN 'PARTIAL_HISTORY' THEN 'PARTIAL_HISTORY'
           WHEN 'MISMATCH' THEN 'MISMATCH'
           ELSE 'INSUFFICIENT_DATA'
       END,
       holding_start_confidence = CASE holding_start_status
           WHEN 'RECONCILED' THEN 'HIGH'
           WHEN 'PARTIAL_HISTORY' THEN 'LOW'
           ELSE 'NONE'
       END
 WHERE item_type = 'POSITION_SNAPSHOT';

ALTER TABLE ledger.portfolio_import_items
    ADD CONSTRAINT portfolio_import_items_holding_start_method_check CHECK (
        holding_start_method IN (
            'TRANSACTION_RECONCILED', 'PORTFOLIO_ACTIVITY_ESTIMATE',
            'PARTIAL_HISTORY', 'MISMATCH', 'INSUFFICIENT_DATA'
        )
    ),
    ADD CONSTRAINT portfolio_import_items_holding_start_confidence_check CHECK (
        holding_start_confidence IN ('HIGH', 'MEDIUM', 'LOW', 'NONE')
    ),
    ADD CONSTRAINT portfolio_import_items_holding_start_reasons_check CHECK (
        array_position(holding_start_reasons, NULL) IS NULL
    );

COMMENT ON COLUMN ledger.portfolio_import_items.holding_start_method IS
    'System inference method for candidate_holding_started_on; user overrides remain separate.';
COMMENT ON COLUMN ledger.portfolio_import_items.holding_start_confidence IS
    'Confidence of the system candidate: HIGH, MEDIUM, LOW, or NONE.';
COMMENT ON COLUMN ledger.portfolio_import_items.holding_start_reasons IS
    'Structured reason codes explaining limitations and evidence used by the inference.';

ALTER TABLE ledger.position_period_metrics
    DROP CONSTRAINT position_period_metrics_holding_start_method_check,
    ADD COLUMN holding_start_confidence TEXT NOT NULL DEFAULT 'NONE',
    ADD CONSTRAINT position_period_metrics_holding_start_method_check CHECK (
        holding_start_method IN (
            'TRANSACTION_RECONCILED', 'PORTFOLIO_ACTIVITY_ESTIMATE',
            'PARTIAL_HISTORY', 'MISMATCH', 'INSUFFICIENT_DATA', 'USER_CONFIRMED'
        )
    ),
    ADD CONSTRAINT position_period_metrics_holding_start_confidence_check CHECK (
        holding_start_confidence IN ('HIGH', 'MEDIUM', 'LOW', 'NONE')
    );

UPDATE ledger.position_period_metrics
   SET holding_start_confidence = CASE holding_start_method
       WHEN 'TRANSACTION_RECONCILED' THEN 'HIGH'
       WHEN 'USER_CONFIRMED' THEN 'HIGH'
       WHEN 'PARTIAL_HISTORY' THEN 'LOW'
       ELSE 'NONE'
   END;

COMMENT ON COLUMN ledger.position_period_metrics.holding_start_confidence IS
    'Confidence copied from the effective candidate, or HIGH for a user-confirmed date.';
