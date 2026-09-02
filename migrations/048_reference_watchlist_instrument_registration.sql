BEGIN;

-- Register a previously unseen instrument when a user follows it from the
-- search result ("我的关注" 直接关注基金).
--
-- Watchlist follows require an authoritative `reference.instruments` row.  When
-- the user picks an OTC fund / ETF that is not yet in the master, this function
-- creates the minimal master record (asset_type / primary_market / currency /
-- canonical_name) plus a TICKER alias, and returns the canonical identity.  It
-- is SECURITY DEFINER so the API role can register instruments without holding
-- direct INSERT on the curated reference tables; callers must pre-normalise
-- asset_type to the internal enum ('OFF_EXCHANGE_FUND'/'EXCHANGE_FUND') and pass
-- a market value that satisfies the instruments CHECK constraint.  It exposes
-- no dynamic SQL and fixes search_path.

CREATE OR REPLACE FUNCTION reference.ensure_watchlist_instrument(
    p_symbol TEXT,
    p_name TEXT,
    p_asset_type TEXT,
    p_market TEXT,
    p_currency TEXT DEFAULT 'CNY'
) RETURNS TABLE (
    instrument_id UUID,
    symbol TEXT,
    asset_name TEXT,
    asset_type TEXT,
    primary_market TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = reference, pg_catalog
AS $$
DECLARE
    v_instrument_id UUID;
BEGIN
    -- Reuse any existing ACTIVE master row that already owns this TICKER.
    SELECT alias.instrument_id INTO v_instrument_id
      FROM reference.instrument_aliases alias
      JOIN reference.instruments instrument ON instrument.id = alias.instrument_id
     WHERE alias.alias_type = 'TICKER'
       AND upper(alias.alias_value) = upper(btrim(p_symbol))
       AND instrument.lifecycle_status = 'ACTIVE'
       AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
     ORDER BY alias.effective_from DESC
     LIMIT 1;

    IF v_instrument_id IS NULL THEN
        INSERT INTO reference.instruments (
            asset_type, primary_market, trading_currency, canonical_name,
            lifecycle_status, metadata
        )
        VALUES (
            p_asset_type, p_market, p_currency, btrim(p_name), 'ACTIVE',
            jsonb_build_object(
                'registeredBy', 'watchlist-follow',
                'source', 'user-follow'
            )
        )
        RETURNING id INTO v_instrument_id;

        BEGIN
            INSERT INTO reference.instrument_aliases (
                instrument_id, alias_type, alias_value, market, effective_from
            )
            VALUES (
                v_instrument_id, 'TICKER', upper(btrim(p_symbol)),
                p_market, CURRENT_DATE
            )
            ON CONFLICT DO NOTHING;
        EXCEPTION WHEN unique_violation THEN
            -- A concurrent registration won the same (alias, market, day);
            -- the final SELECT below still returns a canonical row.
            NULL;
        END;
    END IF;

    RETURN QUERY
        SELECT instrument.id,
               upper(btrim(p_symbol)),
               instrument.canonical_name,
               instrument.asset_type,
               instrument.primary_market
          FROM reference.instruments instrument
         WHERE instrument.id = v_instrument_id;
END;
$$;

REVOKE ALL ON FUNCTION reference.ensure_watchlist_instrument(
    TEXT, TEXT, TEXT, TEXT, TEXT
) FROM PUBLIC, authenticated, wealthsail_worker;
GRANT EXECUTE ON FUNCTION reference.ensure_watchlist_instrument(
    TEXT, TEXT, TEXT, TEXT, TEXT
) TO wealthsail_api;

COMMIT;
