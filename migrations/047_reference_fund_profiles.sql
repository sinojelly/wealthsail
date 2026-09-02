BEGIN;

-- Reference fund profiles: per-OTC-fund subscription/redemption openness used
-- by the PRE_MARKET briefing's "今日提醒" (cutoff/reminder) section.
--
-- instrument_cutoff_rules only expresses *deadlines* (cutoff_time <= 15:00) --
-- it never says whether a fund is currently open for subscription/redemption.
-- This table carries that openness so the briefing can distinguish, for each
-- held fund:
--   * OPEN       -> 可正常申赎（配合 cutoff rule 输出"今日 15:00 前可提交申购/赎回"）
--   * SUSPENDED  -> 暂停（含暂停申购/暂停赎回/限大额，由回填方按公告口径落库）
-- Reference ingestion fills rows with an effective window; an absent row means
-- "no verified status", which renders as "今日无申赎截止提醒" (never a guess).

CREATE TABLE IF NOT EXISTS reference.fund_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id) ON DELETE CASCADE,
    purchase_status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (purchase_status IN ('OPEN', 'SUSPENDED')),
    redemption_status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (redemption_status IN ('OPEN', 'SUSPENDED')),
    effective_from DATE NOT NULL,
    effective_to DATE,
    source_id UUID REFERENCES reference.data_sources(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    UNIQUE (instrument_id, effective_from)
);

-- The PRE_MARKET report reads these through the API transaction role only.
-- Browser and worker roles must not read or write reference fund status
-- directly, mirroring the boundary enforced for instrument_cutoff_rules.
REVOKE ALL ON reference.fund_profiles FROM PUBLIC, authenticated, wealthsail_worker;
GRANT SELECT ON reference.fund_profiles TO wealthsail_api;

COMMIT;
