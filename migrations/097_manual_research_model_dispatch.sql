-- A saved request freezes the explicit entry mode. Existing requests stay DEBUG.
ALTER TABLE research.daily_research_refresh_requests
 ADD COLUMN request_mode TEXT NOT NULL DEFAULT 'DEBUG'
   CHECK(request_mode IN ('DEBUG','MANUAL')),
 ADD CONSTRAINT daily_research_request_identity_mode UNIQUE(id,request_mode);
GRANT INSERT(request_mode) ON research.daily_research_refresh_requests
 TO wealthsail_api,wealthsail_worker;

-- Keep the historical request column and DEBUG indexes for compatibility.
ALTER TABLE market.alpha_public_batches
 DROP CONSTRAINT alpha_public_batches_run_mode_check,
 DROP CONSTRAINT alpha_public_mode_request,
 ADD CONSTRAINT alpha_public_batches_run_mode_check
   CHECK(run_mode IN ('SCHEDULED','DEBUG','MANUAL')),
 ADD CONSTRAINT alpha_public_mode_request
   CHECK((run_mode IN ('DEBUG','MANUAL'))=(debug_request_id IS NOT NULL)),
 ADD CONSTRAINT alpha_public_request_mode FOREIGN KEY(debug_request_id,run_mode)
   REFERENCES research.daily_research_refresh_requests(id,request_mode),
 ADD CONSTRAINT alpha_public_batch_mode UNIQUE(id,run_mode),
 ADD CONSTRAINT alpha_public_batch_request_mode UNIQUE(id,debug_request_id,run_mode);
CREATE UNIQUE INDEX alpha_public_manual_request
 ON market.alpha_public_batches(debug_request_id) WHERE run_mode='MANUAL';

ALTER TABLE research.daily_research_runs
 DROP CONSTRAINT daily_research_runs_run_mode_check,
 DROP CONSTRAINT daily_research_mode_request,
 DROP CONSTRAINT daily_research_debug_source_required,
 ADD CONSTRAINT daily_research_runs_run_mode_check
   CHECK(run_mode IN ('SCHEDULED','DEBUG','MANUAL')),
 ADD CONSTRAINT daily_research_mode_request
   CHECK((run_mode IN ('DEBUG','MANUAL'))=(debug_request_id IS NOT NULL)),
 ADD CONSTRAINT daily_research_debug_source_required
   CHECK(run_mode='SCHEDULED' OR source_batch_id IS NOT NULL),
 ADD CONSTRAINT daily_research_request_mode FOREIGN KEY(debug_request_id,run_mode)
   REFERENCES research.daily_research_refresh_requests(id,request_mode),
 ADD CONSTRAINT daily_research_source_mode FOREIGN KEY(source_batch_id,run_mode)
   REFERENCES market.alpha_public_batches(id,run_mode),
 ADD CONSTRAINT daily_research_source_request_mode
   FOREIGN KEY(source_batch_id,debug_request_id,run_mode)
   REFERENCES market.alpha_public_batches(id,debug_request_id,run_mode);
CREATE UNIQUE INDEX daily_research_manual_request
 ON research.daily_research_runs(debug_request_id) WHERE run_mode='MANUAL';

-- The model occupation is durable and has no TTL/reclaim operation. A timeout,
-- cancellation, lost connection or expired observation window cannot release it.
CREATE TABLE research.daily_research_model_guards (
 run_id UUID PRIMARY KEY REFERENCES research.daily_research_runs(run_id),
 owner_token UUID NOT NULL,
 acquired_at TIMESTAMPTZ NOT NULL,
 released_at TIMESTAMPTZ,
 CHECK(released_at IS NULL OR released_at>=acquired_at)
);
CREATE UNIQUE INDEX daily_research_one_active_model
 ON research.daily_research_model_guards((true)) WHERE released_at IS NULL;
ALTER TABLE research.daily_research_model_guards ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.daily_research_model_guards FORCE ROW LEVEL SECURITY;
REVOKE ALL ON research.daily_research_model_guards FROM PUBLIC,authenticated,wealthsail_api;
GRANT SELECT,INSERT ON research.daily_research_model_guards TO wealthsail_worker;
GRANT UPDATE(released_at) ON research.daily_research_model_guards TO wealthsail_worker;
COMMENT ON TABLE research.daily_research_model_guards IS
 'Global model cost guard across scheduled, manual and debug research. Only confirmed model exit permits owner-token release; no automatic expiry or retry.';
COMMENT ON COLUMN research.daily_research_refresh_requests.request_mode IS
 'Frozen explicit request mode; legacy commands remain DEBUG and idempotency mappings never change.';
