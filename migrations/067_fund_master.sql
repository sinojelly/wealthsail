BEGIN;

-- 067: Market-wide fund master data backing name-based search in the
-- fund-screener.  One row per open-ended mainland mutual fund, refreshed
-- weekly by the Worker from AkShare's fund list.  API reads; only the Worker
-- writes, through the SECURITY DEFINER upsert below (066 lesson: grant every
-- privilege a flow needs in the same migration that introduces it).

INSERT INTO reference.data_sources (
    name, source_type, license_model, allowed_usage, priority, status
)
VALUES (
    'AKSHARE_FUND_MASTER',
    'FUND_NAV',
    'OPEN_SOURCE',
    'INTERNAL_REPORTING',
    36,
    'ACTIVE'
)
ON CONFLICT (name) DO UPDATE
SET source_type = EXCLUDED.source_type,
    license_model = EXCLUDED.license_model,
    allowed_usage = EXCLUDED.allowed_usage,
    priority = EXCLUDED.priority,
    status = EXCLUDED.status;

CREATE TABLE reference.fund_master (
    fund_code TEXT PRIMARY KEY CHECK (fund_code ~ '^[0-9]{6}$'),
    fund_name TEXT NOT NULL,
    fund_type TEXT NOT NULL,
    pinyin_abbr TEXT,
    pinyin_full TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reference.upsert_fund_master(
    p_fund_code TEXT,
    p_fund_name TEXT,
    p_fund_type TEXT,
    p_pinyin_abbr TEXT,
    p_pinyin_full TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = reference, pg_catalog
AS $$
BEGIN
    IF p_fund_code !~ '^[0-9]{6}$'
       OR NULLIF(btrim(p_fund_name), '') IS NULL
       OR NULLIF(btrim(p_fund_type), '') IS NULL THEN
        RAISE EXCEPTION 'invalid fund master identity';
    END IF;

    INSERT INTO reference.fund_master (
        fund_code, fund_name, fund_type, pinyin_abbr, pinyin_full, updated_at
    )
    VALUES (
        p_fund_code, btrim(p_fund_name), btrim(p_fund_type),
        p_pinyin_abbr, p_pinyin_full, now()
    )
    ON CONFLICT (fund_code) DO UPDATE
    SET fund_name = EXCLUDED.fund_name,
        fund_type = EXCLUDED.fund_type,
        pinyin_abbr = EXCLUDED.pinyin_abbr,
        pinyin_full = EXCLUDED.pinyin_full,
        updated_at = now()
    WHERE reference.fund_master.fund_name IS DISTINCT FROM EXCLUDED.fund_name
       OR reference.fund_master.fund_type IS DISTINCT FROM EXCLUDED.fund_type
       OR reference.fund_master.pinyin_abbr IS DISTINCT FROM EXCLUDED.pinyin_abbr
       OR reference.fund_master.pinyin_full IS DISTINCT FROM EXCLUDED.pinyin_full;
    RETURN TRUE;
END;
$$;

REVOKE ALL ON reference.fund_master FROM PUBLIC, authenticated;
GRANT SELECT ON reference.fund_master TO wealthsail_api, wealthsail_worker;
REVOKE ALL ON FUNCTION reference.upsert_fund_master(
    TEXT, TEXT, TEXT, TEXT, TEXT
) FROM PUBLIC, authenticated, wealthsail_api;
GRANT EXECUTE ON FUNCTION reference.upsert_fund_master(
    TEXT, TEXT, TEXT, TEXT, TEXT
) TO wealthsail_worker;

COMMENT ON TABLE reference.fund_master IS
    'Market-wide open-ended fund identity list from AkShare fund_name_em; backing name-based search and identity resolution for on-demand fetches.';
COMMENT ON FUNCTION reference.upsert_fund_master(
    TEXT, TEXT, TEXT, TEXT, TEXT
) IS 'Controlled Worker write path for the shared fund master list.';

COMMIT;
