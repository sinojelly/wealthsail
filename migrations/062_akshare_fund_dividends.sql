BEGIN;

-- 062: register the public fund-dividend source and make its event type an
-- explicit corporate-action fact.  Existing Worker write restrictions remain
-- unchanged: writes continue through market.upsert_corporate_action_sync only.

INSERT INTO reference.data_sources (
    name, source_type, license_model, allowed_usage, priority, status
)
VALUES (
    'AKSHARE_FUND_DIVIDEND_ACTIONS',
    'MARKET_DATA',
    'OPEN_SOURCE',
    'INTERNAL_REPORTING',
    25,
    'ACTIVE'
)
ON CONFLICT (name) DO UPDATE
SET source_type = EXCLUDED.source_type,
    license_model = EXCLUDED.license_model,
    allowed_usage = EXCLUDED.allowed_usage,
    priority = EXCLUDED.priority,
    status = EXCLUDED.status;

ALTER TABLE market.corporate_actions
    DROP CONSTRAINT IF EXISTS corporate_actions_action_type_check,
    ADD CONSTRAINT corporate_actions_action_type_check CHECK (
        action_type IN (
            'CASH_DIVIDEND', 'STOCK_DIVIDEND', 'FUND_DIVIDEND', 'SPLIT',
            'MERGER', 'CODE_CHANGE', 'DELISTING', 'EX_RIGHTS',
            'RIGHTS_ISSUE', 'OTHER'
        )
    );

-- Preserve the least-privilege boundary after altering the table contract.
REVOKE ALL ON market.corporate_actions
    FROM PUBLIC, authenticated, wealthsail_worker;
GRANT SELECT ON market.corporate_actions TO wealthsail_api;

COMMIT;
