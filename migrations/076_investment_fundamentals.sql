BEGIN;
ALTER TABLE reference.research_indices
ADD COLUMN valuation JSONB NOT NULL DEFAULT '{}'::jsonb
CHECK(jsonb_typeof(valuation)='object');
CREATE TABLE market.investment_fundamental_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_kind TEXT NOT NULL CHECK(subject_kind IN ('index','fund')),
    subject_key TEXT NOT NULL,
    data_kind TEXT NOT NULL CHECK(data_kind IN ('VALUATION','COSTS')),
    input_hash TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    as_of DATE,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    result JSONB NOT NULL CHECK(jsonb_typeof(result)='object'),
    UNIQUE(subject_kind,subject_key,data_kind,input_hash)
);
CREATE INDEX investment_fundamental_latest
ON market.investment_fundamental_snapshots(subject_kind,subject_key,data_kind,retrieved_at DESC);
REVOKE ALL ON market.investment_fundamental_snapshots FROM PUBLIC,authenticated;
GRANT SELECT ON market.investment_fundamental_snapshots TO wealthsail_api,wealthsail_worker;
GRANT INSERT ON market.investment_fundamental_snapshots TO wealthsail_worker;
COMMIT;
