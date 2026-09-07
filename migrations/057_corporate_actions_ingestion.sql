BEGIN;

-- 057: make corporate actions importable, revisioned and date-addressable.
--
-- The original migration 004 table only modeled ex-dates and had no ingestion
-- identity or readable summary.  PRE_MARKET therefore could neither represent
-- a payment/rights/other event cleanly nor import the same CSV idempotently.
-- Keep the existing market fact table as the single source of truth and evolve
-- it in place; do not create a duplicate reference.corporate_actions table.

INSERT INTO reference.data_sources (
    name, source_type, license_model, allowed_usage, priority, status
)
VALUES (
    'MANUAL_CORPORATE_ACTIONS',
    'MARKET_DATA',
    'USER_SUPPLIED',
    'INTERNAL_REPORTING',
    50,
    'ACTIVE'
)
ON CONFLICT (name) DO UPDATE
SET source_type = EXCLUDED.source_type,
    license_model = EXCLUDED.license_model,
    allowed_usage = EXCLUDED.allowed_usage,
    priority = EXCLUDED.priority,
    status = EXCLUDED.status;

ALTER TABLE market.corporate_actions
    ADD COLUMN IF NOT EXISTS action_date DATE,
    ADD COLUMN IF NOT EXISTS summary TEXT,
    ADD COLUMN IF NOT EXISTS source_event_id TEXT,
    ADD COLUMN IF NOT EXISTS source_ref TEXT;

-- Existing rows remain valid, queryable facts.  The legacy identifier is only
-- an ingestion identity; it does not pretend that an external provider supplied
-- an event id.  Existing params and source_id provenance are preserved.
UPDATE market.corporate_actions
SET action_date = COALESCE(action_date, ex_date),
    summary = COALESCE(
        NULLIF(btrim(summary), ''),
        CASE action_type
            WHEN 'CASH_DIVIDEND' THEN '现金分红除息'
            WHEN 'STOCK_DIVIDEND' THEN '送股除权'
            WHEN 'SPLIT' THEN '拆股除权'
            WHEN 'MERGER' THEN '合并事项'
            WHEN 'CODE_CHANGE' THEN '代码变更'
            WHEN 'DELISTING' THEN '退市事项'
            ELSE '公司行动'
        END
    ),
    source_event_id = COALESCE(
        NULLIF(btrim(source_event_id), ''),
        'legacy:' || id::text
    );

ALTER TABLE market.corporate_actions
    ALTER COLUMN ex_date DROP NOT NULL,
    ALTER COLUMN action_date SET NOT NULL,
    ALTER COLUMN summary SET NOT NULL,
    ALTER COLUMN source_event_id SET NOT NULL;

ALTER TABLE market.corporate_actions
    DROP CONSTRAINT IF EXISTS corporate_actions_action_type_check,
    DROP CONSTRAINT IF EXISTS corporate_actions_instrument_id_action_type_ex_date_revision_key,
    DROP CONSTRAINT IF EXISTS corporate_actions_source_event_id_revision_key,
    DROP CONSTRAINT IF EXISTS corporate_actions_summary_check,
    DROP CONSTRAINT IF EXISTS corporate_actions_source_event_id_check,
    DROP CONSTRAINT IF EXISTS corporate_actions_revision_check;

ALTER TABLE market.corporate_actions
    ADD CONSTRAINT corporate_actions_action_type_check CHECK (
        action_type IN (
            'CASH_DIVIDEND', 'STOCK_DIVIDEND', 'SPLIT', 'MERGER',
            'CODE_CHANGE', 'DELISTING', 'EX_RIGHTS', 'RIGHTS_ISSUE', 'OTHER'
        )
    ),
    ADD CONSTRAINT corporate_actions_summary_check CHECK (
        length(btrim(summary)) BETWEEN 1 AND 500
    ),
    ADD CONSTRAINT corporate_actions_source_event_id_check CHECK (
        length(btrim(source_event_id)) BETWEEN 1 AND 200
    ),
    ADD CONSTRAINT corporate_actions_revision_check CHECK (revision > 0),
    ADD CONSTRAINT corporate_actions_source_event_id_revision_key
        UNIQUE (source_event_id, revision);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_date_instrument
    ON market.corporate_actions(action_date, instrument_id);

COMMENT ON COLUMN market.corporate_actions.action_date IS
    'Primary event date used by reports; ex/record/pay dates retain lifecycle detail.';
COMMENT ON COLUMN market.corporate_actions.source_event_id IS
    'Stable, globally namespaced ingestion id; corrections append a higher revision.';

-- Preserve the migration-042 least-privilege boundary after altering the table.
REVOKE ALL ON market.corporate_actions
    FROM PUBLIC, authenticated, wealthsail_worker;
GRANT SELECT ON market.corporate_actions TO wealthsail_api;

COMMIT;
