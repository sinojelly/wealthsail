BEGIN;
CREATE TABLE market.opportunity_series_observations (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),code TEXT NOT NULL,reference_date DATE NOT NULL,
 frequency TEXT NOT NULL CHECK(frequency IN ('DAILY','MONTHLY','YTD')),value NUMERIC,unit TEXT NOT NULL,
 source_uri TEXT NOT NULL,source_name TEXT NOT NULL,published_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ NOT NULL,
 available_at TIMESTAMPTZ NOT NULL,time_basis TEXT NOT NULL CHECK(time_basis IN ('OFFICIAL_PUBLICATION','RETRIEVAL_ONLY')),
 method_version TEXT NOT NULL,metadata JSONB NOT NULL DEFAULT '{}',canonical_hash TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>0),CHECK(value IS NULL OR value::text NOT IN ('NaN','Infinity','-Infinity')),
 CHECK(time_basis<>'RETRIEVAL_ONLY' OR available_at=retrieved_at),
 CHECK(time_basis<>'OFFICIAL_PUBLICATION' OR (published_at IS NOT NULL AND available_at>=published_at)),
 UNIQUE(code,reference_date,source_uri,method_version,revision)
);
CREATE INDEX opportunity_series_readlatest ON market.opportunity_series_observations(code,reference_date,revision DESC,retrieved_at DESC);
CREATE TABLE reference.opportunity_source_permissions (
 source_key TEXT PRIMARY KEY,status TEXT NOT NULL CHECK(status IN ('UNKNOWN','APPROVED','DENIED')),
 purpose TEXT NOT NULL,evidence_uri TEXT,verified_at TIMESTAMPTZ,valid_until TIMESTAMPTZ,
 CHECK(status<>'APPROVED' OR (verified_at IS NOT NULL AND length(btrim(COALESCE(evidence_uri,'')))>0))
);
INSERT INTO reference.opportunity_source_permissions(source_key,status,purpose)
 VALUES('CHINABOND','UNKNOWN','investment_research_display'),('CFETS','UNKNOWN','investment_research_display');
CREATE TABLE research.opportunity_strategy_validations (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),instrument_id UUID NOT NULL REFERENCES reference.instruments(id),
 method_version TEXT NOT NULL,dataset_hash TEXT NOT NULL,config_hash TEXT NOT NULL,
 evaluated_at TIMESTAMPTZ NOT NULL,available_at TIMESTAMPTZ NOT NULL,retrieved_at TIMESTAMPTZ NOT NULL,
 result JSONB NOT NULL CHECK(jsonb_typeof(result)='object'),provenance JSONB NOT NULL CHECK(jsonb_typeof(provenance)='object'),
 CHECK(result->>'state' IS DISTINCT FROM 'PASS' OR (
   COALESCE(provenance->>'sourceMode','')='REAL_PIT' AND COALESCE(provenance->>'pointInTimeVerified','')='true')),
 UNIQUE(instrument_id,method_version,dataset_hash,config_hash)
);
CREATE INDEX opportunity_strategy_validation_latest ON research.opportunity_strategy_validations(instrument_id,available_at DESC);
CREATE TABLE risk.opportunity_condition_monitors (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,user_id UUID NOT NULL,
 direction_id TEXT REFERENCES reference.research_directions(id),index_id TEXT REFERENCES reference.research_indices(id),
 instrument_id UUID REFERENCES reference.instruments(id),fund_code TEXT,fund_market TEXT,scope_key TEXT NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE,enabled_at TIMESTAMPTZ NOT NULL DEFAULT now(),created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 UNIQUE(tenant_id,user_id,scope_key),UNIQUE(tenant_id,user_id,id)
);
CREATE TABLE risk.opportunity_decision_snapshots (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,user_id UUID NOT NULL,monitor_id UUID NOT NULL,
 enabled_at TIMESTAMPTZ NOT NULL,as_of TIMESTAMPTZ NOT NULL,method_version TEXT NOT NULL,
 input_hash TEXT NOT NULL,condition_fingerprint TEXT NOT NULL,result JSONB NOT NULL CHECK(jsonb_typeof(result)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 FOREIGN KEY(tenant_id,user_id,monitor_id) REFERENCES risk.opportunity_condition_monitors(tenant_id,user_id,id),
 UNIQUE(tenant_id,user_id,id),UNIQUE(tenant_id,user_id,monitor_id,enabled_at,as_of,input_hash,method_version)
);
CREATE INDEX opportunity_decision_baseline ON risk.opportunity_decision_snapshots(monitor_id,enabled_at,as_of DESC);
CREATE TABLE risk.opportunity_condition_notices (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,user_id UUID NOT NULL,monitor_id UUID NOT NULL,
 snapshot_id UUID NOT NULL,enabled_at TIMESTAMPTZ NOT NULL,
 fingerprint TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL,
 severity TEXT NOT NULL CHECK(severity IN ('INFO','WATCH','CRITICAL')),created_at TIMESTAMPTZ NOT NULL DEFAULT now(),read_at TIMESTAMPTZ,
 before_conditions JSONB NOT NULL,after_conditions JSONB NOT NULL,
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 FOREIGN KEY(tenant_id,user_id,snapshot_id) REFERENCES risk.opportunity_decision_snapshots(tenant_id,user_id,id),
 FOREIGN KEY(tenant_id,user_id,monitor_id) REFERENCES risk.opportunity_condition_monitors(tenant_id,user_id,id),
 UNIQUE(tenant_id,user_id,monitor_id,enabled_at,fingerprint)
);
GRANT USAGE ON SCHEMA research,risk TO wealthsail_api,wealthsail_worker;
REVOKE ALL ON market.opportunity_series_observations,reference.opportunity_source_permissions,
 research.opportunity_strategy_validations,risk.opportunity_condition_monitors,
 risk.opportunity_decision_snapshots,risk.opportunity_condition_notices FROM PUBLIC,authenticated;
GRANT SELECT ON market.opportunity_series_observations,reference.opportunity_source_permissions,
 research.opportunity_strategy_validations,risk.opportunity_condition_monitors,
 risk.opportunity_decision_snapshots,risk.opportunity_condition_notices TO wealthsail_api,wealthsail_worker;
GRANT INSERT ON market.opportunity_series_observations,research.opportunity_strategy_validations,
 risk.opportunity_decision_snapshots,risk.opportunity_condition_notices TO wealthsail_worker;
GRANT INSERT ON risk.opportunity_condition_monitors TO wealthsail_api;
GRANT UPDATE(enabled,enabled_at) ON risk.opportunity_condition_monitors TO wealthsail_api;
GRANT UPDATE(read_at) ON risk.opportunity_condition_notices TO wealthsail_api;
ALTER TABLE risk.opportunity_condition_monitors ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk.opportunity_condition_monitors FORCE ROW LEVEL SECURITY;
ALTER TABLE risk.opportunity_decision_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk.opportunity_decision_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE risk.opportunity_condition_notices ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk.opportunity_condition_notices FORCE ROW LEVEL SECURITY;
CREATE POLICY monitor_actor_read ON risk.opportunity_condition_monitors FOR SELECT TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
CREATE POLICY monitor_actor_insert ON risk.opportunity_condition_monitors FOR INSERT TO wealthsail_api
 WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
CREATE POLICY monitor_actor_toggle ON risk.opportunity_condition_monitors FOR UPDATE TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id)) WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
CREATE POLICY decision_actor_read ON risk.opportunity_decision_snapshots FOR SELECT TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
CREATE POLICY notice_actor_read ON risk.opportunity_condition_notices FOR SELECT TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
CREATE POLICY notice_actor_ack ON risk.opportunity_condition_notices FOR UPDATE TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id)) WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
COMMIT;
