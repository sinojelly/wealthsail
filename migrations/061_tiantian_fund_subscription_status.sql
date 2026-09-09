BEGIN;

-- 061: persist the verified current subscription/redemption state of held
-- off-exchange funds without giving the Worker direct write access to the
-- reference table consumed by PRE_MARKET reports.

INSERT INTO reference.data_sources (
    name, source_type, license_model, allowed_usage, priority, status
)
VALUES (
    'TIANTIAN_FUND_STATUS',
    'MARKET_DATA',
    'OPEN_SOURCE',
    'INTERNAL_REPORTING',
    20,
    'ACTIVE'
)
ON CONFLICT (name) DO UPDATE
SET source_type = EXCLUDED.source_type,
    license_model = EXCLUDED.license_model,
    allowed_usage = EXCLUDED.allowed_usage,
    priority = EXCLUDED.priority,
    status = EXCLUDED.status;

ALTER TABLE reference.fund_profiles
    ADD COLUMN IF NOT EXISTS source_ref TEXT,
    ADD COLUMN IF NOT EXISTS source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMPTZ;

ALTER TABLE reference.fund_profiles
    DROP CONSTRAINT IF EXISTS fund_profiles_source_metadata_object_check,
    ADD CONSTRAINT fund_profiles_source_metadata_object_check CHECK (
        jsonb_typeof(source_metadata) = 'object'
    );

CREATE OR REPLACE FUNCTION reference.upsert_fund_profile_status(
    p_instrument_id UUID,
    p_purchase_status TEXT,
    p_redemption_status TEXT,
    p_effective_from DATE,
    p_source_ref TEXT,
    p_source_metadata JSONB,
    p_retrieved_at TIMESTAMPTZ
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = reference, pg_catalog
AS $$
DECLARE
    v_source_id UUID;
    v_existing reference.fund_profiles%ROWTYPE;
BEGIN
    IF p_instrument_id IS NULL
       OR p_purchase_status NOT IN ('OPEN', 'SUSPENDED')
       OR p_redemption_status NOT IN ('OPEN', 'SUSPENDED')
       OR p_effective_from IS NULL
       OR NULLIF(btrim(p_source_ref), '') IS NULL
       OR p_source_metadata IS NULL
       OR jsonb_typeof(p_source_metadata) <> 'object'
       OR p_retrieved_at IS NULL THEN
        RAISE EXCEPTION 'invalid fund subscription status payload';
    END IF;

    SELECT id
      INTO v_source_id
      FROM reference.data_sources
     WHERE name = 'TIANTIAN_FUND_STATUS'
       AND status = 'ACTIVE';
    IF v_source_id IS NULL THEN
        RAISE EXCEPTION 'TIANTIAN_FUND_STATUS source is not active';
    END IF;

    SELECT profile.*
      INTO v_existing
      FROM reference.fund_profiles profile
     WHERE profile.instrument_id = p_instrument_id
       AND profile.effective_from <= p_effective_from
     ORDER BY profile.effective_from DESC
     LIMIT 1
     FOR UPDATE;

    IF FOUND
       AND (v_existing.effective_to IS NULL OR v_existing.effective_to >= p_effective_from)
       AND v_existing.purchase_status = p_purchase_status
       AND v_existing.redemption_status = p_redemption_status THEN
        UPDATE reference.fund_profiles
           SET source_id = v_source_id,
               source_ref = p_source_ref,
               source_metadata = p_source_metadata,
               retrieved_at = p_retrieved_at,
               updated_at = now()
         WHERE id = v_existing.id;
        RETURN FALSE;
    END IF;

    -- A corrected snapshot for the same effective date replaces that day's
    -- state rather than creating an impossible second daily profile row.
    IF FOUND AND v_existing.effective_from = p_effective_from THEN
        UPDATE reference.fund_profiles
           SET purchase_status = p_purchase_status,
               redemption_status = p_redemption_status,
               source_id = v_source_id,
               source_ref = p_source_ref,
               source_metadata = p_source_metadata,
               retrieved_at = p_retrieved_at,
               updated_at = now()
         WHERE id = v_existing.id;
        RETURN TRUE;
    END IF;

    IF FOUND AND v_existing.effective_to IS NULL THEN
        UPDATE reference.fund_profiles
           SET effective_to = p_effective_from - 1,
               updated_at = now()
         WHERE id = v_existing.id;
    END IF;

    INSERT INTO reference.fund_profiles (
        instrument_id,
        purchase_status,
        redemption_status,
        effective_from,
        source_id,
        source_ref,
        source_metadata,
        retrieved_at
    )
    VALUES (
        p_instrument_id,
        p_purchase_status,
        p_redemption_status,
        p_effective_from,
        v_source_id,
        p_source_ref,
        p_source_metadata,
        p_retrieved_at
    );
    RETURN TRUE;
END;
$$;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON reference.fund_profiles
    FROM wealthsail_worker;
REVOKE ALL ON FUNCTION reference.upsert_fund_profile_status(
    UUID, TEXT, TEXT, DATE, TEXT, JSONB, TIMESTAMPTZ
) FROM PUBLIC, authenticated, wealthsail_api;
GRANT EXECUTE ON FUNCTION reference.upsert_fund_profile_status(
    UUID, TEXT, TEXT, DATE, TEXT, JSONB, TIMESTAMPTZ
) TO wealthsail_worker;

COMMENT ON FUNCTION reference.upsert_fund_profile_status(
    UUID, TEXT, TEXT, DATE, TEXT, JSONB, TIMESTAMPTZ
) IS 'Controlled, effective-dated write path for the TianTian fund subscription-status snapshot.';

COMMIT;
