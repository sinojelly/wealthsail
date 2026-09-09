BEGIN;

-- 060: cache the market-wide portion of a pre-market holding trend once per
-- instrument and Shanghai trade date.  The cache has no tenant, account, or
-- quantity data, so it is safe to reuse across personalised reports.
CREATE TABLE IF NOT EXISTS market.instrument_trend_snapshots (
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id),
    trade_date DATE NOT NULL,
    previous_direction TEXT,
    streak_direction TEXT,
    streak_days INTEGER,
    streak_change_pct NUMERIC(12, 6),
    history_start_date DATE,
    history_end_date DATE,
    history_field_code TEXT,
    quality_status TEXT NOT NULL,
    method_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (instrument_id, trade_date),
    CONSTRAINT instrument_trend_snapshots_previous_direction_check CHECK (
        previous_direction IS NULL OR previous_direction IN ('UP', 'DOWN', 'FLAT')
    ),
    CONSTRAINT instrument_trend_snapshots_streak_direction_check CHECK (
        streak_direction IS NULL OR streak_direction IN ('UP', 'DOWN')
    ),
    CONSTRAINT instrument_trend_snapshots_streak_days_check CHECK (
        streak_days IS NULL OR streak_days BETWEEN 1 AND 30
    ),
    CONSTRAINT instrument_trend_snapshots_history_field_code_check CHECK (
        history_field_code IS NULL OR history_field_code IN ('CLOSE', 'NAV')
    ),
    CONSTRAINT instrument_trend_snapshots_quality_status_check CHECK (
        quality_status IN ('FRESH', 'UNAVAILABLE')
    ),
    CONSTRAINT instrument_trend_snapshots_history_range_check CHECK (
        history_start_date IS NULL
        OR history_end_date IS NULL
        OR history_start_date <= history_end_date
    )
);

CREATE INDEX IF NOT EXISTS idx_instrument_trend_snapshots_trade_date
    ON market.instrument_trend_snapshots (trade_date, instrument_id);

CREATE OR REPLACE FUNCTION market.upsert_instrument_trend_snapshot(
    p_instrument_id UUID,
    p_trade_date DATE,
    p_previous_direction TEXT,
    p_streak_direction TEXT,
    p_streak_days INTEGER,
    p_streak_change_pct NUMERIC,
    p_history_start_date DATE,
    p_history_end_date DATE,
    p_history_field_code TEXT,
    p_quality_status TEXT,
    p_method_version TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, market, reference
AS $$
DECLARE
    v_inserted BOOLEAN;
BEGIN
    IF p_instrument_id IS NULL
       OR p_trade_date IS NULL
       OR p_quality_status NOT IN ('FRESH', 'UNAVAILABLE')
       OR NULLIF(btrim(p_method_version), '') IS NULL
       OR (p_previous_direction IS NOT NULL
           AND p_previous_direction NOT IN ('UP', 'DOWN', 'FLAT'))
       OR (p_streak_direction IS NOT NULL
           AND p_streak_direction NOT IN ('UP', 'DOWN'))
       OR (p_streak_days IS NOT NULL AND p_streak_days NOT BETWEEN 1 AND 30)
       OR (p_history_field_code IS NOT NULL
           AND p_history_field_code NOT IN ('CLOSE', 'NAV'))
       OR (p_history_start_date IS NOT NULL AND p_history_end_date IS NOT NULL
           AND p_history_start_date > p_history_end_date) THEN
        RAISE EXCEPTION 'invalid instrument trend snapshot payload';
    END IF;

    INSERT INTO market.instrument_trend_snapshots (
        instrument_id,
        trade_date,
        previous_direction,
        streak_direction,
        streak_days,
        streak_change_pct,
        history_start_date,
        history_end_date,
        history_field_code,
        quality_status,
        method_version
    )
    VALUES (
        p_instrument_id,
        p_trade_date,
        p_previous_direction,
        p_streak_direction,
        p_streak_days,
        p_streak_change_pct,
        p_history_start_date,
        p_history_end_date,
        p_history_field_code,
        p_quality_status,
        p_method_version
    )
    ON CONFLICT (instrument_id, trade_date) DO NOTHING
    RETURNING TRUE INTO v_inserted;

    RETURN COALESCE(v_inserted, FALSE);
END;
$$;

REVOKE ALL ON market.instrument_trend_snapshots
    FROM PUBLIC, authenticated, wealthsail_api, wealthsail_worker;
GRANT SELECT ON market.instrument_trend_snapshots
    TO wealthsail_api, wealthsail_worker;

REVOKE ALL ON FUNCTION market.upsert_instrument_trend_snapshot(
    UUID, DATE, TEXT, TEXT, INTEGER, NUMERIC, DATE, DATE, TEXT, TEXT, TEXT
) FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION market.upsert_instrument_trend_snapshot(
    UUID, DATE, TEXT, TEXT, INTEGER, NUMERIC, DATE, DATE, TEXT, TEXT, TEXT
) TO wealthsail_api, wealthsail_worker;

-- Runtime roles must derive the trend only through the validated function.
REVOKE INSERT, UPDATE, DELETE ON market.instrument_trend_snapshots
    FROM PUBLIC, authenticated, wealthsail_api, wealthsail_worker;

COMMENT ON TABLE market.instrument_trend_snapshots IS
    'Global per-instrument pre-market trend cache; contains no tenant-specific facts.';
COMMENT ON FUNCTION market.upsert_instrument_trend_snapshot(
    UUID, DATE, TEXT, TEXT, INTEGER, NUMERIC, DATE, DATE, TEXT, TEXT, TEXT
) IS
    'Validated insert-once path for a pre-market instrument trend snapshot.';

COMMIT;
