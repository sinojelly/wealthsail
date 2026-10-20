BEGIN;
CREATE TABLE market.fund_nav_chart_histories (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 fund_code TEXT NOT NULL CHECK(fund_code ~ '^[0-9]{6}$'),
 retrieved_at TIMESTAMPTZ NOT NULL,available_at TIMESTAMPTZ NOT NULL,
 payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object')
);
CREATE INDEX fund_nav_chart_latest ON market.fund_nav_chart_histories(fund_code,retrieved_at DESC,id DESC);
REVOKE ALL ON market.fund_nav_chart_histories FROM PUBLIC,authenticated;
GRANT SELECT ON market.fund_nav_chart_histories TO wealthsail_api,wealthsail_worker;
GRANT INSERT ON market.fund_nav_chart_histories TO wealthsail_worker;
ALTER TABLE workflow.job_runs DROP CONSTRAINT job_runs_job_type_check;
ALTER TABLE workflow.job_runs ADD CONSTRAINT job_runs_job_type_check CHECK(
 job_type IS NULL OR job_type IN ('FUND_NAV_BACKFILL','POSITION_PERIOD_METRICS_REBUILD','FUND_NAV_RANGE_FETCH','INVESTMENT_RESEARCH_REFRESH','FUND_UNIT_NAV_HISTORY_FETCH')
);
CREATE INDEX fund_nav_chart_claimable ON workflow.job_runs(status,available_at,next_retry_at,lease_until)
 WHERE job_type='FUND_UNIT_NAV_HISTORY_FETCH';
COMMIT;
