BEGIN;

-- Register twelve OTC funds the user follows into reference.instruments,
-- keeping the same shape that the watchlist auto-registration path (049)
-- uses: asset_type=OFF_EXCHANGE_FUND, primary_market=FUND_CN, a
-- canonical "code name" label plus a TICKER alias so fund_lookthrough and
-- the fund 14:30 brief can resolve holdings by the 6-digit code.
--
-- Idempotent: a fund is skipped when it already has a TICKER alias or an
-- instrument row with the same canonical name (existing portfolio imports
-- or 049 registrations are never duplicated).

WITH requested (code, canonical_name) AS (
    VALUES
        ('001632', '001632 天弘中证食品饮料ETF联接C'),
        ('012733', '012733 易方达中证人工智能主题ETF联接A'),
        ('011103', '011103 天弘中证光伏产业指数C'),
        ('000689', '000689 前海开源新经济混合A'),
        ('008552', '008552 东财医药C'),
        ('161726', '161726 招商国证生物医药指数(LOF)A'),
        ('070019', '070019 嘉实价值优势混合A'),
        ('002593', '002593 富国美丽中国混合A'),
        ('008295', '008295 朱雀企业优胜C'),
        ('004476', '004476 景顺长城沪港深领先科技股票A'),
        ('001705', '001705 泓德战略转型股票'),
        ('005275', '005275 中欧创新成长灵活配置混合A')
),
-- skip anything already resolvable by code alias or canonical label
missing AS (
    SELECT requested.code, requested.canonical_name
      FROM requested
     WHERE NOT EXISTS (
               SELECT 1
                 FROM reference.instruments instrument
                 JOIN reference.instrument_aliases alias
                   ON alias.instrument_id = instrument.id
                WHERE alias.alias_type = 'TICKER'
                  AND alias.alias_value = requested.code
                  AND alias.effective_from <= CURRENT_DATE
                  AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
           )
       AND NOT EXISTS (
               SELECT 1
                 FROM reference.instruments instrument
                WHERE instrument.canonical_name = requested.canonical_name
           )
),
inserted AS (
    INSERT INTO reference.instruments (
        asset_type, primary_market, trading_currency, canonical_name,
        lifecycle_status, metadata
    )
    SELECT 'OFF_EXCHANGE_FUND', 'FUND_CN', 'CNY', missing.canonical_name,
           'ACTIVE',
           jsonb_build_object(
               'registeredBy', 'user-batch',
               'source', 'manual-import-2026-09-03'
           )
      FROM missing
    RETURNING 1
),
-- Always (re)ensure the TICKER alias for every requested code so lookups by
-- the 6-digit code keep working even when the instrument pre-existed.
aliased AS (
    INSERT INTO reference.instrument_aliases (
        instrument_id, alias_type, alias_value, market, effective_from
    )
    SELECT instrument.id, 'TICKER', requested.code, 'FUND_CN', CURRENT_DATE
      FROM requested
      JOIN reference.instruments instrument
        ON instrument.canonical_name = requested.canonical_name
    ON CONFLICT DO NOTHING
    RETURNING 1
)
SELECT (SELECT count(*) FROM inserted) AS inserted_count,
       (SELECT count(*) FROM aliased) AS alias_count;

COMMIT;
