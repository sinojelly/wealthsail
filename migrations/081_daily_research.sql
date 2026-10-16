BEGIN;

-- Inserting this row consumes the Shanghai calendar slot permanently, before
-- starting a model process. A crash or expired execution window cannot reclaim it.
CREATE TABLE research.daily_research_runs (
 run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 slot_date DATE NOT NULL UNIQUE,
 scheduled_for TIMESTAMPTZ NOT NULL,
 dispatched_at TIMESTAMPTZ NOT NULL,
 input_hash TEXT NOT NULL,
 prompt_version TEXT NOT NULL,
 public_packet JSONB NOT NULL CHECK(jsonb_typeof(public_packet)='object'),
 cli_version TEXT,
 CHECK(slot_date=(scheduled_for AT TIME ZONE 'Asia/Shanghai')::date),
 CHECK(dispatched_at>=scheduled_for)
);
CREATE TABLE research.daily_research_results (
 run_id UUID PRIMARY KEY REFERENCES research.daily_research_runs(run_id),
 status TEXT NOT NULL CHECK(status IN ('READY','FAILED','UNCERTAIN')),
 recorded_at TIMESTAMPTZ NOT NULL,
 payload JSONB CHECK(payload IS NULL OR jsonb_typeof(payload)='object'),
 error_class TEXT CHECK(error_class IS NULL OR error_class ~ '^[A-Za-z][A-Za-z0-9_]{0,79}$'),
 CHECK((status='READY' AND payload IS NOT NULL AND error_class IS NULL)
    OR (status<>'READY' AND payload IS NULL))
);
CREATE INDEX daily_research_results_recent ON research.daily_research_results(recorded_at DESC);
REVOKE ALL ON research.daily_research_runs,research.daily_research_results FROM PUBLIC,authenticated;
GRANT SELECT ON research.daily_research_runs,research.daily_research_results TO wealthsail_api,wealthsail_worker;
GRANT INSERT ON research.daily_research_runs,research.daily_research_results TO wealthsail_worker;
COMMIT;
