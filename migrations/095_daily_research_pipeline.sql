CREATE TABLE research.daily_research_pipelines (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 slot_date DATE NOT NULL UNIQUE,
 scheduled_for TIMESTAMPTZ NOT NULL,
 start_window_ends_at TIMESTAMPTZ NOT NULL,
 runtime_deadline_at TIMESTAMPTZ NOT NULL,
 started_at TIMESTAMPTZ NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL,
 finished_at TIMESTAMPTZ,
 state TEXT NOT NULL CHECK (state IN ('RUNNING','READY','FAILED','UNCERTAIN','MISSED')),
 stage TEXT NOT NULL CHECK (stage IN ('COLLECTING','WAITING_SOURCES','VALIDATING_INPUT','MODEL_RUNNING','VERIFYING_OUTPUT','PERSISTING')),
 lease_token UUID NOT NULL,
 lease_until TIMESTAMPTZ NOT NULL,
 source_batch_id UUID REFERENCES market.alpha_public_batches(id),
 research_run_id UUID REFERENCES research.daily_research_runs(run_id),
 error_class TEXT,
 quality JSONB,
 CHECK (finished_at IS NULL OR finished_at>=started_at),
 CHECK (updated_at>=started_at),
 CHECK (start_window_ends_at>scheduled_for),
 CHECK (runtime_deadline_at>started_at),
 CHECK (quality IS NULL OR jsonb_typeof(quality)='object')
);
ALTER TABLE research.daily_research_pipelines ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.daily_research_pipelines FORCE ROW LEVEL SECURITY;
CREATE POLICY daily_pipeline_public_read ON research.daily_research_pipelines
 FOR SELECT TO wealthsail_api USING (true);
REVOKE ALL ON research.daily_research_pipelines FROM PUBLIC,authenticated;
GRANT SELECT ON research.daily_research_pipelines TO wealthsail_api,wealthsail_worker;
GRANT INSERT,UPDATE ON research.daily_research_pipelines TO wealthsail_worker;
COMMENT ON TABLE research.daily_research_pipelines IS
 'One owned research pipeline per scheduled Shanghai date. Source collection precedes the immutable model cost barrier; public status contains no prompt or credentials.';
