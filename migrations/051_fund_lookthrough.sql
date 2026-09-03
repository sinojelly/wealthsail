BEGIN;



-- Market fund lookthrough: each OTC fund's holdings as published in its
-- periodic report.  The 14:30 report's "今日估值" / "估值可信度" sections are
-- derived from this snapshot (weighted by ``weight_pct`` against each
-- holding's live return rate).
--
-- Rows are append-only per (fund, holding, report_period); new disclosures
-- land as additional rows so historical data stays auditable.  The read path
-- keeps only the latest ``report_period`` per fund at fetch time.

CREATE TABLE IF NOT EXISTS market.fund_lookthrough (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fund_instrument_id UUID NOT NULL REFERENCES reference.instruments(id) ON DELETE CASCADE,
    holding_instrument_id UUID NOT NULL REFERENCES reference.instruments(id) ON DELETE CASCADE,
    weight_pct NUMERIC(5, 2) NOT NULL CHECK (weight_pct >= 0 AND weight_pct <= 100),
    report_period DATE NOT NULL,
    source_id UUID REFERENCES reference.data_sources(id),
    as_of TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (fund_instrument_id, holding_instrument_id, report_period)
);

CREATE INDEX IF NOT EXISTS idx_fund_lookthrough_fund_asof
    ON market.fund_lookthrough (fund_instrument_id, as_of DESC);

CREATE INDEX IF NOT EXISTS idx_fund_lookthrough_holding
    ON market.fund_lookthrough (holding_instrument_id);

-- Reports read these through the API transaction role; the worker only reads
-- (it produces the briefing but never updates lookthrough).  Boundary mirrors
-- reference.fund_profiles.
REVOKE ALL ON market.fund_lookthrough FROM PUBLIC, authenticated, wealthsail_worker;
GRANT SELECT ON market.fund_lookthrough TO wealthsail_api, wealthsail_worker;

COMMIT;