BEGIN;

-- 058: synchronise A-share / exchange-fund corporate actions with source-level
-- provenance.  market.corporate_actions remains the only durable fact table;
-- Worker writes are limited to the controlled function defined below.

INSERT INTO reference.data_sources (
    name, source_type, license_model, allowed_usage, priority, status
)
VALUES
    (
        'AKSHARE_CORPORATE_ACTIONS',
        'MARKET_DATA',
        'OPEN_SOURCE',
        'INTERNAL_REPORTING',
        20,
        'ACTIVE'
    ),
    (
        'BAOSTOCK_CORPORATE_ACTIONS',
        'MARKET_DATA',
        'OPEN_SOURCE',
        'INTERNAL_REPORTING',
        30,
        'ACTIVE'
    )
ON CONFLICT (name) DO UPDATE
SET source_type = EXCLUDED.source_type,
    license_model = EXCLUDED.license_model,
    allowed_usage = EXCLUDED.allowed_usage,
    priority = EXCLUDED.priority,
    status = EXCLUDED.status;

ALTER TABLE market.corporate_actions
    ADD COLUMN IF NOT EXISTS quality_status TEXT,
    ADD COLUMN IF NOT EXISTS verified_by TEXT[];

UPDATE market.corporate_actions
SET quality_status = COALESCE(quality_status, 'FRESH'),
    verified_by = COALESCE(verified_by, ARRAY['MANUAL']::TEXT[]);

ALTER TABLE market.corporate_actions
    ALTER COLUMN quality_status SET DEFAULT 'FRESH',
    ALTER COLUMN quality_status SET NOT NULL,
    ALTER COLUMN verified_by SET DEFAULT ARRAY['MANUAL']::TEXT[],
    ALTER COLUMN verified_by SET NOT NULL,
    DROP CONSTRAINT IF EXISTS corporate_actions_quality_status_check,
    ADD CONSTRAINT corporate_actions_quality_status_check CHECK (
        quality_status IN ('FRESH', 'STALE', 'UNAVAILABLE', 'CONFLICT')
    );

CREATE OR REPLACE FUNCTION market.upsert_corporate_action_sync(
    p_instrument_id UUID,
    p_action_type TEXT,
    p_action_date DATE,
    p_record_date DATE,
    p_pay_date DATE,
    p_params JSONB,
    p_source_event_id TEXT,
    p_source_ref TEXT,
    p_quality_status TEXT,
    p_verified_by TEXT[]
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = market, reference, pg_catalog
AS $$
DECLARE
    v_existing market.corporate_actions%ROWTYPE;
    v_source_id UUID;
    v_revision INTEGER;
    v_action_id UUID;
BEGIN
    IF p_instrument_id IS NULL
       OR p_action_type IS NULL
       OR p_action_date IS NULL
       OR NULLIF(btrim(p_source_event_id), '') IS NULL
       OR p_quality_status NOT IN ('FRESH', 'STALE', 'UNAVAILABLE', 'CONFLICT')
       OR COALESCE(array_length(p_verified_by, 1), 0) = 0 THEN
        RAISE EXCEPTION 'invalid corporate action sync payload';
    END IF;

    SELECT id
      INTO v_source_id
      FROM reference.data_sources
     WHERE name = p_verified_by[1]
       AND status = 'ACTIVE';
    IF v_source_id IS NULL THEN
        RAISE EXCEPTION 'unknown active corporate action source';
    END IF;

    SELECT *
      INTO v_existing
      FROM market.corporate_actions
     WHERE source_event_id = p_source_event_id
     ORDER BY revision DESC
     LIMIT 1
     FOR UPDATE;

    IF FOUND
       AND v_existing.instrument_id = p_instrument_id
       AND v_existing.action_type = p_action_type
       AND v_existing.action_date = p_action_date
       AND v_existing.record_date IS NOT DISTINCT FROM p_record_date
       AND v_existing.pay_date IS NOT DISTINCT FROM p_pay_date
       AND v_existing.params = COALESCE(p_params, '{}'::JSONB) THEN
        UPDATE market.corporate_actions
           SET quality_status = p_quality_status,
               verified_by = (
                   SELECT array_agg(DISTINCT source ORDER BY source)
                     FROM unnest(v_existing.verified_by || p_verified_by) AS source
               ),
               source_ref = COALESCE(p_source_ref, v_existing.source_ref),
               source_id = v_source_id
         WHERE id = v_existing.id
         RETURNING id INTO v_action_id;
        RETURN v_action_id;
    END IF;

    v_revision := COALESCE(v_existing.revision, 0) + 1;
    INSERT INTO market.corporate_actions (
        instrument_id,
        action_type,
        action_date,
        ex_date,
        record_date,
        pay_date,
        params,
        source_id,
        source_event_id,
        source_ref,
        quality_status,
        verified_by,
        summary,
        revision
    )
    VALUES (
        p_instrument_id,
        p_action_type,
        p_action_date,
        p_action_date,
        p_record_date,
        p_pay_date,
        COALESCE(p_params, '{}'::JSONB),
        v_source_id,
        p_source_event_id,
        p_source_ref,
        p_quality_status,
        p_verified_by,
        CASE p_action_type
            WHEN 'CASH_DIVIDEND' THEN '现金分红除息'
            WHEN 'EX_RIGHTS' THEN '除权除息'
            WHEN 'RIGHTS_ISSUE' THEN '配股事项'
            ELSE '公司行动'
        END,
        v_revision
    )
    RETURNING id INTO v_action_id;

    RETURN v_action_id;
END;
$$;

REVOKE ALL ON FUNCTION market.upsert_corporate_action_sync(
    UUID, TEXT, DATE, DATE, DATE, JSONB, TEXT, TEXT, TEXT, TEXT[]
) FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION market.upsert_corporate_action_sync(
    UUID, TEXT, DATE, DATE, DATE, JSONB, TEXT, TEXT, TEXT, TEXT[]
) TO wealthsail_worker;

-- Keep direct table writes unavailable to runtime roles after adding columns.
REVOKE ALL ON market.corporate_actions
    FROM PUBLIC, authenticated, wealthsail_worker;
GRANT SELECT ON market.corporate_actions TO wealthsail_api;

COMMENT ON COLUMN market.corporate_actions.quality_status IS
    'Quality of the event after provider reconciliation.';
COMMENT ON COLUMN market.corporate_actions.verified_by IS
    'Provider names that corroborated this event, or MANUAL for CSV input.';
COMMENT ON FUNCTION market.upsert_corporate_action_sync(
    UUID, TEXT, DATE, DATE, DATE, JSONB, TEXT, TEXT, TEXT, TEXT[]
) IS
    'Controlled, revisioned write path for Worker corporate-action sync.';

COMMIT;
