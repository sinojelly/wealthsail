BEGIN;
INSERT INTO reference.macro_indicator_catalog(field_code,label,dimension,max_age_days)
VALUES ('PPI_YOY','PPI 同比','inflation',45), ('M2_YOY','M2 同比','liquidity',45)
ON CONFLICT(field_code) DO NOTHING;
CREATE INDEX investment_macro_history_read
ON market.market_observations(field_code,as_of,revision DESC)
WHERE subject_type='MACRO_SERIES';
COMMIT;
