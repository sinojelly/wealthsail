BEGIN;
CREATE TABLE market.investment_analysis_snapshots (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 subject_kind TEXT NOT NULL CHECK(subject_kind IN ('index','fund')),
 subject_key TEXT NOT NULL, input_hash TEXT NOT NULL,
 source_uri TEXT NOT NULL, series_kind TEXT NOT NULL,
 as_of DATE NOT NULL, available_at TIMESTAMPTZ NOT NULL,
 retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 method_version TEXT NOT NULL, result JSONB NOT NULL, points JSONB NOT NULL,
 UNIQUE(subject_kind,subject_key,input_hash,method_version)
);
CREATE INDEX investment_analysis_latest ON market.investment_analysis_snapshots(subject_kind,subject_key,retrieved_at DESC);
REVOKE ALL ON market.investment_analysis_snapshots FROM PUBLIC, authenticated;
GRANT SELECT ON market.investment_analysis_snapshots TO wealthsail_api, wealthsail_worker;
GRANT INSERT ON market.investment_analysis_snapshots TO wealthsail_worker;

ALTER TABLE workflow.job_runs DROP CONSTRAINT job_runs_job_type_check;
ALTER TABLE workflow.job_runs ADD CONSTRAINT job_runs_job_type_check CHECK(
 job_type IS NULL OR job_type IN ('FUND_NAV_BACKFILL','POSITION_PERIOD_METRICS_REBUILD','FUND_NAV_RANGE_FETCH','INVESTMENT_RESEARCH_REFRESH')
);
CREATE INDEX investment_refresh_claimable ON workflow.job_runs(status,available_at,next_retry_at,lease_until)
 WHERE job_type='INVESTMENT_RESEARCH_REFRESH';
COMMIT;
