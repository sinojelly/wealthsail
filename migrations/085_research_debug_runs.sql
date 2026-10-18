BEGIN;

-- Revalidate an operator's current tenant before public research execution.
GRANT SELECT(id,status) ON identity.tenants TO wealthsail_worker;
GRANT SELECT(id,email) ON identity.profiles TO wealthsail_worker;

CREATE TABLE research.daily_research_refresh_requests (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL,user_id UUID NOT NULL,command_key UUID NOT NULL,
 requested_at TIMESTAMPTZ NOT NULL,
 status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','RUNNING','READY','FAILED','UNCERTAIN')),
 started_at TIMESTAMPTZ,completed_at TIMESTAMPTZ,
 batch_id UUID REFERENCES market.alpha_public_batches(id),
 report_run_id UUID REFERENCES research.daily_research_runs(run_id),
 error_class TEXT CHECK(error_class IS NULL OR error_class ~ '^[A-Za-z][A-Za-z0-9_]{0,79}$'),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 UNIQUE(tenant_id,user_id,command_key),
 UNIQUE(id,tenant_id,user_id),
 CHECK(started_at IS NULL OR started_at>=requested_at),
 CHECK(completed_at IS NULL OR completed_at>=COALESCE(started_at,requested_at)),
 CHECK((status='QUEUED' AND started_at IS NULL AND completed_at IS NULL)
    OR (status='RUNNING' AND started_at IS NOT NULL AND completed_at IS NULL)
    OR (status IN ('READY','FAILED','UNCERTAIN') AND completed_at IS NOT NULL)),
 CHECK(status<>'READY' OR (report_run_id IS NOT NULL AND batch_id IS NOT NULL AND error_class IS NULL))
);
CREATE INDEX daily_research_refresh_queue ON research.daily_research_refresh_requests(requested_at,id) WHERE status='QUEUED';
CREATE INDEX daily_research_refresh_actor ON research.daily_research_refresh_requests(tenant_id,user_id,requested_at DESC,id DESC);
ALTER TABLE research.daily_research_refresh_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.daily_research_refresh_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY debug_research_actor_read ON research.daily_research_refresh_requests FOR SELECT TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id) AND identity.has_tenant_role(tenant_id,ARRAY['OWNER','ADMIN']));
CREATE POLICY debug_research_actor_create ON research.daily_research_refresh_requests FOR INSERT TO wealthsail_api
 WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id) AND identity.has_tenant_role(tenant_id,ARRAY['OWNER','ADMIN'])
 AND status='QUEUED' AND started_at IS NULL AND completed_at IS NULL AND batch_id IS NULL AND report_run_id IS NULL AND error_class IS NULL);
REVOKE ALL ON research.daily_research_refresh_requests FROM PUBLIC,authenticated;
GRANT SELECT ON research.daily_research_refresh_requests TO wealthsail_api,wealthsail_worker;
GRANT INSERT(tenant_id,user_id,command_key,requested_at) ON research.daily_research_refresh_requests TO wealthsail_api,wealthsail_worker;
GRANT UPDATE(status,started_at,completed_at,batch_id,report_run_id,error_class) ON research.daily_research_refresh_requests TO wealthsail_worker;

-- Every command key that coalesces into a live request remains replayable even
-- after that request finishes; no later replay can spawn a fresh model call.
CREATE TABLE research.daily_research_refresh_commands (
 tenant_id UUID NOT NULL,user_id UUID NOT NULL,command_key UUID NOT NULL,
 request_id UUID NOT NULL,requested_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(tenant_id,user_id,command_key),
 FOREIGN KEY(request_id,tenant_id,user_id) REFERENCES research.daily_research_refresh_requests(id,tenant_id,user_id)
);
ALTER TABLE research.daily_research_refresh_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.daily_research_refresh_commands FORCE ROW LEVEL SECURITY;
CREATE POLICY debug_research_command_read ON research.daily_research_refresh_commands FOR SELECT TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id) AND identity.has_tenant_role(tenant_id,ARRAY['OWNER','ADMIN']));
CREATE POLICY debug_research_command_create ON research.daily_research_refresh_commands FOR INSERT TO wealthsail_api
 WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id) AND identity.has_tenant_role(tenant_id,ARRAY['OWNER','ADMIN']));
REVOKE ALL ON research.daily_research_refresh_commands FROM PUBLIC,authenticated;
GRANT SELECT,INSERT ON research.daily_research_refresh_commands TO wealthsail_api,wealthsail_worker;

ALTER TABLE research.daily_research_runs DROP CONSTRAINT daily_research_runs_slot_date_key;
ALTER TABLE research.daily_research_runs ADD COLUMN run_mode TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK(run_mode IN ('SCHEDULED','DEBUG')),
 ADD COLUMN debug_request_id UUID REFERENCES research.daily_research_refresh_requests(id),
 ADD COLUMN source_batch_id UUID REFERENCES market.alpha_public_batches(id),
 ADD CONSTRAINT daily_research_mode_request CHECK((run_mode='DEBUG')=(debug_request_id IS NOT NULL)),
 ADD CONSTRAINT daily_research_debug_source_required CHECK(run_mode<>'DEBUG' OR source_batch_id IS NOT NULL);
CREATE UNIQUE INDEX daily_research_scheduled_slot ON research.daily_research_runs(slot_date) WHERE run_mode='SCHEDULED';
CREATE UNIQUE INDEX daily_research_debug_request ON research.daily_research_runs(debug_request_id) WHERE run_mode='DEBUG';
CREATE INDEX daily_research_latest_dispatch ON research.daily_research_runs(dispatched_at DESC,run_id DESC);

ALTER TABLE market.alpha_public_batches DROP CONSTRAINT alpha_public_batches_run_date_key;
ALTER TABLE market.alpha_public_batches ADD COLUMN run_mode TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK(run_mode IN ('SCHEDULED','DEBUG')),
 ADD COLUMN debug_request_id UUID REFERENCES research.daily_research_refresh_requests(id),
 ADD CONSTRAINT alpha_public_mode_request CHECK((run_mode='DEBUG')=(debug_request_id IS NOT NULL));
CREATE UNIQUE INDEX alpha_public_scheduled_slot ON market.alpha_public_batches(run_date) WHERE run_mode='SCHEDULED';
CREATE UNIQUE INDEX alpha_public_debug_request ON market.alpha_public_batches(debug_request_id) WHERE run_mode='DEBUG';
ALTER TABLE market.alpha_public_batches ADD CONSTRAINT alpha_public_batch_request UNIQUE(id,debug_request_id);
ALTER TABLE research.daily_research_runs ADD CONSTRAINT daily_research_debug_source_identity
 FOREIGN KEY(source_batch_id,debug_request_id) REFERENCES market.alpha_public_batches(id,debug_request_id);

CREATE TABLE market.fund_research_nav_histories (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 fund_code TEXT NOT NULL CHECK(fund_code ~ '^[0-9]{6}$'),
 retrieved_at TIMESTAMPTZ NOT NULL,available_at TIMESTAMPTZ NOT NULL,
 payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object')
);
CREATE INDEX fund_research_nav_latest ON market.fund_research_nav_histories(fund_code,retrieved_at DESC,id DESC);
REVOKE ALL ON market.fund_research_nav_histories FROM PUBLIC,authenticated;
GRANT SELECT ON market.fund_research_nav_histories TO wealthsail_api,wealthsail_worker;
GRANT INSERT ON market.fund_research_nav_histories TO wealthsail_worker;
COMMIT;
