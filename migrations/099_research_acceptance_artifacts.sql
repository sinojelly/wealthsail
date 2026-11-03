-- Acceptance evidence and outcomes are append-only, separate from formal reports.
CREATE TABLE research.research_validation_artifacts (
 run_id UUID PRIMARY KEY REFERENCES research.daily_research_runs(run_id),
 tenant_id UUID REFERENCES identity.tenants(id),
 validated_at TIMESTAMPTZ NOT NULL,
 packet JSONB NOT NULL CHECK(jsonb_typeof(packet)='object' AND octet_length(COALESCE(packet->>'content',''))<=4194304),
 raw_output JSONB NOT NULL CHECK(jsonb_typeof(raw_output)='object' AND octet_length(COALESCE(raw_output->>'content',''))<=2097152),
 source_checks JSONB NOT NULL CHECK(jsonb_typeof(source_checks)='object' AND octet_length(COALESCE(source_checks->>'content',''))<=524288),
 CHECK(octet_length(packet::text)<=26214400 AND octet_length(raw_output::text)<=13107200 AND octet_length(source_checks::text)<=3276800)
);
CREATE TABLE research.research_validation_records (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 run_id UUID NOT NULL REFERENCES research.daily_research_runs(run_id),
 tenant_id UUID REFERENCES identity.tenants(id),
 kind TEXT NOT NULL CHECK(kind IN ('INITIAL','REPLAY')),
 actor_id UUID REFERENCES identity.profiles(id),
 actor_tenant_id UUID REFERENCES identity.tenants(id),
 command_key UUID,
 validator_version TEXT NOT NULL CHECK(validator_version ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$'),
 validated_at TIMESTAMPTZ NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL,
 outcome JSONB NOT NULL CHECK(jsonb_typeof(outcome)='object' AND octet_length(outcome::text)<=1048576),
 summary JSONB NOT NULL CHECK(jsonb_typeof(summary)='object' AND octet_length(summary::text)<=1048576),
 FOREIGN KEY(actor_tenant_id,actor_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 CHECK((kind='INITIAL' AND actor_id IS NULL AND actor_tenant_id IS NULL AND command_key IS NULL)
    OR (kind='REPLAY' AND actor_id IS NOT NULL AND actor_tenant_id IS NOT NULL AND command_key IS NOT NULL)),
 CHECK(tenant_id IS NULL OR actor_tenant_id IS NULL OR tenant_id=actor_tenant_id)
);
CREATE UNIQUE INDEX research_validation_initial_once ON research.research_validation_records(run_id) WHERE kind='INITIAL';
CREATE UNIQUE INDEX research_validation_replay_command ON research.research_validation_records(actor_tenant_id,actor_id,command_key) WHERE kind='REPLAY';
CREATE INDEX research_validation_replay_history ON research.research_validation_records(run_id,recorded_at DESC,id) WHERE kind='REPLAY';

CREATE FUNCTION research.validation_current_tenant() RETURNS UUID
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT NULLIF((NULLIF(current_setting('request.jwt.claims',true),'')::jsonb)->>'tenant_id','')::uuid
$$;
CREATE FUNCTION research.validation_live_member(p_tenant UUID,p_admin BOOLEAN) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p_tenant IS NOT NULL AND p_tenant=research.validation_current_tenant() AND EXISTS(
   SELECT 1 FROM identity.tenant_members m JOIN identity.tenants t ON t.id=m.tenant_id
   WHERE m.tenant_id=p_tenant AND m.user_id=auth.uid() AND m.status='ACTIVE' AND t.status='ACTIVE'
     AND (NOT p_admin OR m.role IN ('OWNER','ADMIN')))
$$;
REVOKE ALL ON FUNCTION research.validation_current_tenant(),research.validation_live_member(UUID,BOOLEAN) FROM PUBLIC,authenticated;
GRANT EXECUTE ON FUNCTION research.validation_current_tenant(),research.validation_live_member(UUID,BOOLEAN) TO wealthsail_api,wealthsail_worker;

CREATE FUNCTION research.derive_validation_scope() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_tenant UUID; v_mode TEXT; v_dispatch TIMESTAMPTZ;
BEGIN
 SELECT q.tenant_id,r.run_mode,r.dispatched_at INTO v_tenant,v_mode,v_dispatch
 FROM research.daily_research_runs r LEFT JOIN research.daily_research_refresh_requests q ON q.id=r.debug_request_id
 WHERE r.run_id=NEW.run_id;
 IF NOT FOUND OR (v_mode<>'SCHEDULED' AND v_tenant IS NULL) THEN
   RAISE EXCEPTION 'VALIDATION_RUN_SCOPE_INVALID' USING ERRCODE='23503';
 END IF;
 NEW.tenant_id := v_tenant;
 IF NEW.validated_at<v_dispatch THEN
   RAISE EXCEPTION 'VALIDATION_TIME_INVALID' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION research.derive_validation_scope() FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
CREATE TRIGGER derive_artifact_scope BEFORE INSERT ON research.research_validation_artifacts FOR EACH ROW EXECUTE FUNCTION research.derive_validation_scope();
CREATE TRIGGER derive_acceptance_scope BEFORE INSERT ON research.research_validation_records FOR EACH ROW EXECUTE FUNCTION research.derive_validation_scope();

ALTER TABLE research.research_validation_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.research_validation_artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE research.research_validation_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.research_validation_records FORCE ROW LEVEL SECURITY;
REVOKE ALL ON research.research_validation_artifacts,research.research_validation_records FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
GRANT SELECT,INSERT ON research.research_validation_artifacts,research.research_validation_records TO wealthsail_worker;
GRANT SELECT(id,run_id,tenant_id,kind,actor_id,actor_tenant_id,command_key,validator_version,validated_at,recorded_at,summary)
 ON research.research_validation_records TO wealthsail_api;
GRANT INSERT(id,run_id,kind,actor_id,actor_tenant_id,command_key,validator_version,validated_at,recorded_at,outcome,summary)
 ON research.research_validation_records TO wealthsail_api;
CREATE POLICY acceptance_worker_artifact_read ON research.research_validation_artifacts FOR SELECT TO wealthsail_worker USING(true);
CREATE POLICY acceptance_worker_artifact_insert ON research.research_validation_artifacts FOR INSERT TO wealthsail_worker WITH CHECK(true);
CREATE POLICY acceptance_worker_record_read ON research.research_validation_records FOR SELECT TO wealthsail_worker USING(true);
CREATE POLICY acceptance_worker_record_insert ON research.research_validation_records FOR INSERT TO wealthsail_worker WITH CHECK(kind='INITIAL');
-- The migration owner may service the fixed SECURITY DEFINER readers even with FORCE RLS.
CREATE POLICY acceptance_owner_artifact_read ON research.research_validation_artifacts FOR SELECT TO CURRENT_USER USING(true);
CREATE POLICY acceptance_owner_record_read ON research.research_validation_records FOR SELECT TO CURRENT_USER USING(true);
CREATE POLICY acceptance_member_summary ON research.research_validation_records FOR SELECT TO wealthsail_api USING(
 (kind='INITIAL' AND research.validation_live_member(research.validation_current_tenant(),false)
   AND (tenant_id IS NULL OR tenant_id=research.validation_current_tenant())) OR
 (kind='REPLAY' AND actor_tenant_id=research.validation_current_tenant()
   AND research.validation_live_member(actor_tenant_id,true)));
CREATE POLICY acceptance_admin_replay_insert ON research.research_validation_records FOR INSERT TO wealthsail_api WITH CHECK(
 kind='REPLAY' AND actor_id=auth.uid() AND actor_tenant_id=research.validation_current_tenant()
 AND research.validation_live_member(actor_tenant_id,true)
 AND (tenant_id IS NULL OR tenant_id=actor_tenant_id));

CREATE FUNCTION research.read_validation_artifacts_admin(p_run UUID,p_content BOOLEAN DEFAULT false) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a research.research_validation_artifacts%ROWTYPE; v_tenant UUID;
BEGIN
 v_tenant := research.validation_current_tenant();
 IF NOT research.validation_live_member(v_tenant,true) THEN
   RAISE EXCEPTION 'VALIDATION_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO a FROM research.research_validation_artifacts WHERE run_id=p_run AND (tenant_id IS NULL OR tenant_id=v_tenant);
 IF NOT FOUND THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('researchId',a.run_id,'validatedAt',a.validated_at,
   'complete',a.packet->>'content' IS NOT NULL AND a.raw_output->>'content' IS NOT NULL AND a.source_checks->>'content' IS NOT NULL
      AND a.packet->>'omissionReason' IS NULL AND a.raw_output->>'omissionReason' IS NULL AND a.source_checks->>'omissionReason' IS NULL,
   'items',jsonb_build_array(
      a.packet||jsonb_build_object('kind','PACKET','content',CASE WHEN p_content THEN a.packet->>'content' END),
      a.raw_output||jsonb_build_object('kind','RAW_MODEL','content',CASE WHEN p_content THEN a.raw_output->>'content' END),
      a.source_checks||jsonb_build_object('kind','SOURCE_CHECKS','content',CASE WHEN p_content THEN a.source_checks->>'content' END)));
END $$;
CREATE FUNCTION research.read_validation_outcome_admin(p_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_tenant UUID; result JSONB;
BEGIN
 v_tenant := research.validation_current_tenant();
 IF NOT research.validation_live_member(v_tenant,true) THEN
   RAISE EXCEPTION 'VALIDATION_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT outcome INTO result FROM research.research_validation_records WHERE id=p_id
  AND (tenant_id IS NULL OR tenant_id=v_tenant) AND (kind='INITIAL' OR actor_tenant_id=v_tenant);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION research.read_validation_artifacts_admin(UUID,BOOLEAN),research.read_validation_outcome_admin(UUID) FROM PUBLIC,authenticated,wealthsail_worker;
GRANT EXECUTE ON FUNCTION research.read_validation_artifacts_admin(UUID,BOOLEAN),research.read_validation_outcome_admin(UUID) TO wealthsail_api;
COMMENT ON TABLE research.research_validation_artifacts IS 'Private frozen evidence for offline validation only. No runtime UPDATE/DELETE or API direct raw SELECT.';
COMMENT ON TABLE research.research_validation_records IS 'Append-only initial/replay acceptance outcomes. Observations are not formal reports or trading plans.';
