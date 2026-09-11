BEGIN;

-- 064: Shared accumulated-NAV range cache for the fund-screener.  This is a
-- reference read model, never a tenant portfolio or a recommendation record.

INSERT INTO reference.data_sources (
    name, source_type, license_model, allowed_usage, priority, status
)
VALUES (
    'AKSHARE_FUND_NAV_RANGE',
    'FUND_NAV',
    'OPEN_SOURCE',
    'INTERNAL_REPORTING',
    35,
    'ACTIVE'
)
ON CONFLICT (name) DO UPDATE
SET source_type = EXCLUDED.source_type,
    license_model = EXCLUDED.license_model,
    allowed_usage = EXCLUDED.allowed_usage,
    priority = EXCLUDED.priority,
    status = EXCLUDED.status;

CREATE TABLE reference.fund_nav_ranges (
    fund_code TEXT PRIMARY KEY CHECK (fund_code ~ '^[0-9]{6}$'),
    fund_name TEXT NOT NULL,
    fund_type TEXT NOT NULL,
    latest_nav NUMERIC(28, 10),
    latest_nav_date DATE,
    highest_nav NUMERIC(28, 10),
    highest_nav_date DATE,
    lowest_nav NUMERIC(28, 10),
    lowest_nav_date DATE,
    drawdown_from_high NUMERIC(12, 4),
    rise_from_low NUMERIC(12, 4),
    current_percentile NUMERIC(12, 4),
    inception_date DATE,
    data_source TEXT NOT NULL CHECK (data_source IN ('AKSHARE', 'UNAVAILABLE')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (data_source = 'UNAVAILABLE'
         AND latest_nav IS NULL AND latest_nav_date IS NULL
         AND highest_nav IS NULL AND highest_nav_date IS NULL
         AND lowest_nav IS NULL AND lowest_nav_date IS NULL
         AND drawdown_from_high IS NULL AND rise_from_low IS NULL
         AND current_percentile IS NULL AND inception_date IS NULL)
        OR
        (data_source = 'AKSHARE'
         AND latest_nav IS NOT NULL AND latest_nav > 0 AND latest_nav_date IS NOT NULL
         AND highest_nav IS NOT NULL AND highest_nav > 0 AND highest_nav_date IS NOT NULL
         AND lowest_nav IS NOT NULL AND lowest_nav > 0 AND lowest_nav_date IS NOT NULL
         AND inception_date IS NOT NULL
         AND highest_nav >= lowest_nav
         AND latest_nav BETWEEN lowest_nav AND highest_nav
         AND drawdown_from_high IS NOT NULL AND drawdown_from_high BETWEEN 0 AND 100
         AND rise_from_low IS NOT NULL AND rise_from_low >= 0
         AND (current_percentile IS NULL OR current_percentile BETWEEN 0 AND 100))
    )
);

CREATE OR REPLACE FUNCTION reference.upsert_fund_nav_range(
    p_fund_code TEXT,
    p_fund_name TEXT,
    p_fund_type TEXT,
    p_latest_nav NUMERIC,
    p_latest_nav_date DATE,
    p_highest_nav NUMERIC,
    p_highest_nav_date DATE,
    p_lowest_nav NUMERIC,
    p_lowest_nav_date DATE,
    p_drawdown_from_high NUMERIC,
    p_rise_from_low NUMERIC,
    p_current_percentile NUMERIC,
    p_inception_date DATE,
    p_data_source TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = reference, pg_catalog
AS $$
BEGIN
    IF p_fund_code !~ '^[0-9]{6}$'
       OR NULLIF(btrim(p_fund_name), '') IS NULL
       OR NULLIF(btrim(p_fund_type), '') IS NULL
       OR p_data_source NOT IN ('AKSHARE', 'UNAVAILABLE') THEN
        RAISE EXCEPTION 'invalid fund NAV range identity or source';
    END IF;

    IF p_data_source = 'UNAVAILABLE' AND (
        p_latest_nav IS NOT NULL OR p_latest_nav_date IS NOT NULL
        OR p_highest_nav IS NOT NULL OR p_highest_nav_date IS NOT NULL
        OR p_lowest_nav IS NOT NULL OR p_lowest_nav_date IS NOT NULL
        OR p_drawdown_from_high IS NOT NULL OR p_rise_from_low IS NOT NULL
        OR p_current_percentile IS NOT NULL OR p_inception_date IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'unavailable fund NAV range cannot include values';
    END IF;

    IF p_data_source = 'AKSHARE' AND (
        p_latest_nav IS NULL OR p_latest_nav <= 0 OR p_latest_nav_date IS NULL
        OR p_highest_nav IS NULL OR p_highest_nav <= 0 OR p_highest_nav_date IS NULL
        OR p_lowest_nav IS NULL OR p_lowest_nav <= 0 OR p_lowest_nav_date IS NULL
        OR p_inception_date IS NULL OR p_highest_nav < p_lowest_nav
        OR p_latest_nav < p_lowest_nav OR p_latest_nav > p_highest_nav
        OR p_drawdown_from_high IS NULL OR p_drawdown_from_high < 0
        OR p_drawdown_from_high > 100 OR p_rise_from_low IS NULL
        OR p_rise_from_low < 0 OR (p_current_percentile IS NOT NULL
            AND (p_current_percentile < 0 OR p_current_percentile > 100))
    ) THEN
        RAISE EXCEPTION 'invalid available fund NAV range values';
    END IF;

    INSERT INTO reference.fund_nav_ranges (
        fund_code, fund_name, fund_type,
        latest_nav, latest_nav_date, highest_nav, highest_nav_date,
        lowest_nav, lowest_nav_date, drawdown_from_high, rise_from_low,
        current_percentile, inception_date, data_source, updated_at
    )
    VALUES (
        p_fund_code, p_fund_name, p_fund_type,
        p_latest_nav, p_latest_nav_date, p_highest_nav, p_highest_nav_date,
        p_lowest_nav, p_lowest_nav_date, p_drawdown_from_high, p_rise_from_low,
        p_current_percentile, p_inception_date, p_data_source, now()
    )
    ON CONFLICT (fund_code) DO UPDATE
    SET fund_name = EXCLUDED.fund_name,
        fund_type = EXCLUDED.fund_type,
        latest_nav = EXCLUDED.latest_nav,
        latest_nav_date = EXCLUDED.latest_nav_date,
        highest_nav = EXCLUDED.highest_nav,
        highest_nav_date = EXCLUDED.highest_nav_date,
        lowest_nav = EXCLUDED.lowest_nav,
        lowest_nav_date = EXCLUDED.lowest_nav_date,
        drawdown_from_high = EXCLUDED.drawdown_from_high,
        rise_from_low = EXCLUDED.rise_from_low,
        current_percentile = EXCLUDED.current_percentile,
        inception_date = EXCLUDED.inception_date,
        data_source = EXCLUDED.data_source,
        updated_at = now();
    RETURN TRUE;
END;
$$;

REVOKE ALL ON reference.fund_nav_ranges FROM PUBLIC, authenticated, wealthsail_worker;
GRANT SELECT ON reference.fund_nav_ranges TO wealthsail_api;
REVOKE ALL ON FUNCTION reference.upsert_fund_nav_range(
    TEXT, TEXT, TEXT, NUMERIC, DATE, NUMERIC, DATE, NUMERIC, DATE,
    NUMERIC, NUMERIC, NUMERIC, DATE, TEXT
) FROM PUBLIC, authenticated, wealthsail_api;
GRANT EXECUTE ON FUNCTION reference.upsert_fund_nav_range(
    TEXT, TEXT, TEXT, NUMERIC, DATE, NUMERIC, DATE, NUMERIC, DATE,
    NUMERIC, NUMERIC, NUMERIC, DATE, TEXT
) TO wealthsail_worker;

COMMENT ON TABLE reference.fund_nav_ranges IS
    'AkShare accumulated-NAV range cache; UNAVAILABLE rows intentionally contain no numeric facts.';
COMMENT ON FUNCTION reference.upsert_fund_nav_range(
    TEXT, TEXT, TEXT, NUMERIC, DATE, NUMERIC, DATE, NUMERIC, DATE,
    NUMERIC, NUMERIC, NUMERIC, DATE, TEXT
) IS 'Controlled Worker write path for the shared fund-screener NAV range cache.';

COMMIT;
