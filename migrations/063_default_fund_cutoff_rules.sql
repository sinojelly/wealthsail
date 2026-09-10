BEGIN;

-- 063: Every off-exchange fund receives a governed 15:00 China-time baseline.
-- A later effective rule for the same instrument/channel remains authoritative,
-- so QDII and special products can be maintained without changing reports.

INSERT INTO reference.data_sources (
    name, source_type, license_model, allowed_usage, priority, status
)
VALUES (
    'WEALTHSAIL_DEFAULT_FUND_CUTOFF',
    'MARKET_DATA',
    'INTERNAL_POLICY',
    'INTERNAL_REPORTING',
    90,
    'ACTIVE'
)
ON CONFLICT (name) DO UPDATE
SET source_type = EXCLUDED.source_type,
    license_model = EXCLUDED.license_model,
    allowed_usage = EXCLUDED.allowed_usage,
    priority = EXCLUDED.priority,
    status = EXCLUDED.status;

-- The original two-state model collapsed a genuine purchase-limit notice into
-- a suspension.  Preserve LIMITED distinctly; the optional provider-declared
-- amount remains in source_metadata.dailyPurchaseLimit for traceability.
ALTER TABLE reference.fund_profiles
    DROP CONSTRAINT IF EXISTS fund_profiles_purchase_status_check,
    ADD CONSTRAINT fund_profiles_purchase_status_check
        CHECK (purchase_status IN ('OPEN', 'SUSPENDED', 'LIMITED'));

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
       OR p_purchase_status NOT IN ('OPEN', 'SUSPENDED', 'LIMITED')
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
        instrument_id, purchase_status, redemption_status, effective_from,
        source_id, source_ref, source_metadata, retrieved_at
    )
    VALUES (
        p_instrument_id, p_purchase_status, p_redemption_status, p_effective_from,
        v_source_id, p_source_ref, p_source_metadata, p_retrieved_at
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION reference.seed_default_off_exchange_fund_cutoff()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = reference, pg_catalog
AS $$
DECLARE
    v_source_id UUID;
BEGIN
    IF NEW.asset_type <> 'OFF_EXCHANGE_FUND' THEN
        RETURN NEW;
    END IF;

    SELECT id
      INTO v_source_id
      FROM reference.data_sources
     WHERE name = 'WEALTHSAIL_DEFAULT_FUND_CUTOFF'
       AND status = 'ACTIVE';
    IF v_source_id IS NULL THEN
        RAISE EXCEPTION 'WEALTHSAIL_DEFAULT_FUND_CUTOFF source is not active';
    END IF;

    INSERT INTO reference.instrument_cutoff_rules (
        instrument_id, sales_channel, cutoff_time, timezone,
        effective_from, source_id, rule_version
    )
    VALUES (
        NEW.id, 'DEFAULT', TIME '15:00', 'Asia/Shanghai',
        DATE '1970-01-01', v_source_id, 1
    )
    ON CONFLICT (instrument_id, sales_channel, effective_from, rule_version)
    DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS seed_default_off_exchange_fund_cutoff_trigger
    ON reference.instruments;
CREATE TRIGGER seed_default_off_exchange_fund_cutoff_trigger
AFTER INSERT OR UPDATE OF asset_type ON reference.instruments
FOR EACH ROW
WHEN (NEW.asset_type = 'OFF_EXCHANGE_FUND')
EXECUTE FUNCTION reference.seed_default_off_exchange_fund_cutoff();

INSERT INTO reference.instrument_cutoff_rules (
    instrument_id, sales_channel, cutoff_time, timezone,
    effective_from, source_id, rule_version
)
SELECT instrument.id, 'DEFAULT', TIME '15:00', 'Asia/Shanghai',
       DATE '1970-01-01', source.id, 1
  FROM reference.instruments instrument
 CROSS JOIN reference.data_sources source
 WHERE instrument.asset_type = 'OFF_EXCHANGE_FUND'
   AND source.name = 'WEALTHSAIL_DEFAULT_FUND_CUTOFF'
   AND source.status = 'ACTIVE'
ON CONFLICT (instrument_id, sales_channel, effective_from, rule_version)
DO NOTHING;

REVOKE ALL ON FUNCTION reference.seed_default_off_exchange_fund_cutoff() FROM PUBLIC;
REVOKE ALL ON FUNCTION reference.upsert_fund_profile_status(
    UUID, TEXT, TEXT, DATE, TEXT, JSONB, TIMESTAMPTZ
) FROM PUBLIC, authenticated, wealthsail_api;
GRANT EXECUTE ON FUNCTION reference.upsert_fund_profile_status(
    UUID, TEXT, TEXT, DATE, TEXT, JSONB, TIMESTAMPTZ
) TO wealthsail_worker;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON reference.instrument_cutoff_rules
    FROM wealthsail_worker;

COMMENT ON FUNCTION reference.upsert_fund_profile_status(
    UUID, TEXT, TEXT, DATE, TEXT, JSONB, TIMESTAMPTZ
) IS 'Controlled write path for TianTian status; source_metadata.dailyPurchaseLimit is optional and provider-declared only.';

COMMIT;
