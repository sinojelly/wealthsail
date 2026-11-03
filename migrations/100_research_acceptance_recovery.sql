-- Formal publication commits with a bounded frozen outbox. Recovery never reruns research.
CREATE TABLE research.research_validation_archive_queue (
 run_id UUID PRIMARY KEY REFERENCES research.daily_research_runs(run_id),
 tenant_id UUID REFERENCES identity.tenants(id),
 validated_at TIMESTAMPTZ NOT NULL,
 frozen_bundle JSONB NOT NULL CHECK(jsonb_typeof(frozen_bundle)='object' AND octet_length(frozen_bundle::text)<=50331648),
 safe_summary JSONB NOT NULL CHECK(jsonb_typeof(safe_summary)='object' AND octet_length(safe_summary::text)<=1048576),
 status TEXT NOT NULL DEFAULT 'AUDIT_PENDING' CHECK(status IN ('AUDIT_PENDING','ARCHIVING','ARCHIVED','UNRECOVERABLE')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 8),
 lease_token UUID,
 lease_expires_at TIMESTAMPTZ,
 next_attempt_at TIMESTAMPTZ NOT NULL,
 last_error_class TEXT CHECK(last_error_class IN ('ConnectionError','TimeoutError','OSError','PostgresError','InterfaceError','SerializationError','ValidationError_','ArchiveDataError','UnknownArchiveError')),
 created_at TIMESTAMPTZ NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL,
 CHECK((status='ARCHIVING' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status<>'ARCHIVING' AND lease_token IS NULL AND lease_expires_at IS NULL)),
 CHECK(octet_length(COALESCE(frozen_bundle->'artifacts'->'packet'->>'content',''))<=4194304),
 CHECK(octet_length(COALESCE(frozen_bundle->'artifacts'->'raw_output'->>'content',''))<=2097152),
 CHECK(octet_length(COALESCE(frozen_bundle->'artifacts'->'source_checks'->>'content',''))<=524288)
);
CREATE INDEX research_validation_archive_due ON research.research_validation_archive_queue(next_attempt_at,run_id) WHERE status IN ('AUDIT_PENDING','ARCHIVING');
CREATE TRIGGER derive_archive_scope BEFORE INSERT ON research.research_validation_archive_queue FOR EACH ROW EXECUTE FUNCTION research.derive_validation_scope();
ALTER TABLE research.research_validation_archive_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.research_validation_archive_queue FORCE ROW LEVEL SECURITY;
REVOKE ALL ON research.research_validation_archive_queue FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
GRANT SELECT,INSERT ON research.research_validation_archive_queue TO wealthsail_worker;
GRANT UPDATE(status,attempts,lease_token,lease_expires_at,next_attempt_at,last_error_class,updated_at) ON research.research_validation_archive_queue TO wealthsail_worker;
GRANT SELECT(run_id,tenant_id,status,attempts,created_at,updated_at,next_attempt_at,last_error_class,safe_summary) ON research.research_validation_archive_queue TO wealthsail_api;
CREATE POLICY archive_worker_read ON research.research_validation_archive_queue FOR SELECT TO wealthsail_worker USING(true);
CREATE POLICY archive_worker_insert ON research.research_validation_archive_queue FOR INSERT TO wealthsail_worker WITH CHECK(true);
CREATE POLICY archive_worker_update ON research.research_validation_archive_queue FOR UPDATE TO wealthsail_worker USING(true) WITH CHECK(true);
CREATE POLICY archive_owner_read ON research.research_validation_archive_queue FOR SELECT TO CURRENT_USER USING(true);
CREATE POLICY archive_member_summary ON research.research_validation_archive_queue FOR SELECT TO wealthsail_api USING(
 research.validation_live_member(research.validation_current_tenant(),false) AND (tenant_id IS NULL OR tenant_id=research.validation_current_tenant()));

-- Pending evidence is still private. This narrow reader preserves the original descriptors.
CREATE OR REPLACE FUNCTION research.read_validation_artifacts_admin(p_run UUID,p_content BOOLEAN DEFAULT false) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_tenant UUID; v_packet JSONB; v_raw JSONB; v_checks JSONB; v_time TIMESTAMPTZ;
BEGIN
 v_tenant := research.validation_current_tenant();
 IF NOT research.validation_live_member(v_tenant,true) THEN
   RAISE EXCEPTION 'VALIDATION_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT packet,raw_output,source_checks,validated_at INTO v_packet,v_raw,v_checks,v_time
 FROM research.research_validation_artifacts WHERE run_id=p_run AND (tenant_id IS NULL OR tenant_id=v_tenant);
 IF NOT FOUND THEN
   SELECT frozen_bundle->'artifacts'->'packet',frozen_bundle->'artifacts'->'raw_output',frozen_bundle->'artifacts'->'source_checks',validated_at
   INTO v_packet,v_raw,v_checks,v_time FROM research.research_validation_archive_queue
   WHERE run_id=p_run AND (tenant_id IS NULL OR tenant_id=v_tenant);
   IF NOT FOUND THEN RETURN NULL; END IF;
 END IF;
 RETURN jsonb_build_object('researchId',p_run,'validatedAt',v_time,
   'complete',v_packet->>'content' IS NOT NULL AND v_raw->>'content' IS NOT NULL AND v_checks->>'content' IS NOT NULL
      AND v_packet->>'omissionReason' IS NULL AND v_raw->>'omissionReason' IS NULL AND v_checks->>'omissionReason' IS NULL,
   'items',jsonb_build_array(
      v_packet||jsonb_build_object('kind','PACKET','content',CASE WHEN p_content THEN v_packet->>'content' END),
      v_raw||jsonb_build_object('kind','RAW_MODEL','content',CASE WHEN p_content THEN v_raw->>'content' END),
      v_checks||jsonb_build_object('kind','SOURCE_CHECKS','content',CASE WHEN p_content THEN v_checks->>'content' END)));
END $$;
CREATE FUNCTION research.read_validation_archive_outcome_admin(p_run UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result JSONB; v_tenant UUID;
BEGIN
 v_tenant := research.validation_current_tenant();
 IF NOT research.validation_live_member(v_tenant,true) THEN
   RAISE EXCEPTION 'VALIDATION_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT frozen_bundle->'outcome' INTO result FROM research.research_validation_archive_queue
 WHERE run_id=p_run AND (tenant_id IS NULL OR tenant_id=v_tenant);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION research.read_validation_archive_outcome_admin(UUID) FROM PUBLIC,authenticated,wealthsail_worker;
GRANT EXECUTE ON FUNCTION research.read_validation_archive_outcome_admin(UUID) TO wealthsail_api;

-- A failed attempt consumes its reservation. Reusing a command key never consumes twice.
CREATE TABLE research.research_validation_replay_attempts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 run_id UUID NOT NULL REFERENCES research.daily_research_runs(run_id),
 actor_id UUID NOT NULL REFERENCES identity.profiles(id),
 actor_tenant_id UUID NOT NULL REFERENCES identity.tenants(id),
 command_key UUID NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETE','FAILED','TIMED_OUT','CANCELLED')),
 reserved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 completed_at TIMESTAMPTZ,
 reserved_bytes INTEGER NOT NULL DEFAULT 1048576 CHECK(reserved_bytes=1048576),
 result_id UUID REFERENCES research.research_validation_records(id),
 error_code TEXT CHECK(error_code IN ('TIMEOUT','CANCELLED','INVALID_OUTPUT','VALIDATOR_FAILED','MATERIALS_INVALID','OUTPUT_LIMIT')),
 validator_revision TEXT CHECK(validator_revision ~ '^[a-f0-9]{64}$'),
 FOREIGN KEY(actor_tenant_id,actor_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 UNIQUE(actor_tenant_id,actor_id,command_key),
 CHECK((status='RUNNING' AND completed_at IS NULL AND result_id IS NULL AND error_code IS NULL) OR
       (status='COMPLETE' AND completed_at IS NOT NULL AND result_id IS NOT NULL AND error_code IS NULL) OR
       (status IN ('FAILED','TIMED_OUT','CANCELLED') AND completed_at IS NOT NULL AND result_id IS NULL AND error_code IS NOT NULL))
);
CREATE INDEX research_validation_replay_actor_budget ON research.research_validation_replay_attempts(actor_id,reserved_at);
CREATE INDEX research_validation_replay_run_budget ON research.research_validation_replay_attempts(run_id);
ALTER TABLE research.research_validation_replay_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.research_validation_replay_attempts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON research.research_validation_replay_attempts FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
GRANT SELECT ON research.research_validation_replay_attempts TO wealthsail_api;
CREATE POLICY replay_attempt_admin_read ON research.research_validation_replay_attempts FOR SELECT TO wealthsail_api USING(actor_id=auth.uid() AND actor_tenant_id=research.validation_current_tenant() AND research.validation_live_member(actor_tenant_id,true));
CREATE POLICY replay_attempt_owner_all ON research.research_validation_replay_attempts TO CURRENT_USER USING(true) WITH CHECK(true);

CREATE FUNCTION research.reserve_validation_replay(p_run UUID,p_key UUID,p_revision TEXT) RETURNS JSONB
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_tenant UUID; v_actor UUID; v_attempt research.research_validation_replay_attempts%ROWTYPE; v_scope UUID; v_mode TEXT; v_day DATE; v_count BIGINT; v_bytes BIGINT;
BEGIN
 v_tenant := research.validation_current_tenant(); v_actor := auth.uid();
 IF NOT research.validation_live_member(v_tenant,true) THEN
   RAISE EXCEPTION 'VALIDATION_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO v_attempt FROM research.research_validation_replay_attempts WHERE actor_tenant_id=v_tenant AND actor_id=v_actor AND command_key=p_key;
 IF FOUND THEN
   IF v_attempt.run_id<>p_run THEN RETURN jsonb_build_object('status','CONFLICT'); END IF;
   RETURN to_jsonb(v_attempt);
 END IF;
 IF NOT pg_try_advisory_xact_lock(hashtextextended('acceptance:actor:'||v_actor::text,0)) OR
    NOT pg_try_advisory_xact_lock(hashtextextended('acceptance:run:'||p_run::text,0)) THEN
   RETURN jsonb_build_object('status','BUSY');
 END IF;
 SELECT q.tenant_id,r.run_mode INTO v_scope,v_mode FROM research.daily_research_runs r
 LEFT JOIN research.daily_research_refresh_requests q ON q.id=r.debug_request_id WHERE r.run_id=p_run;
 IF NOT FOUND OR (v_scope IS NOT NULL AND v_scope<>v_tenant) OR (v_mode<>'SCHEDULED' AND v_scope IS NULL) THEN
   RETURN jsonb_build_object('status','NOT_FOUND');
 END IF;
 v_day := (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date;
 SELECT count(*) INTO v_count FROM research.research_validation_replay_attempts WHERE actor_id=v_actor AND (reserved_at AT TIME ZONE 'Asia/Shanghai')::date=v_day;
 SELECT v_count+count(*) INTO v_count FROM research.research_validation_records r WHERE kind='REPLAY' AND actor_id=v_actor
 AND (recorded_at AT TIME ZONE 'Asia/Shanghai')::date=v_day AND NOT EXISTS(SELECT 1 FROM research.research_validation_replay_attempts a WHERE a.result_id=r.id);
 IF v_count>=10 THEN RETURN jsonb_build_object('status','ACTOR_LIMIT'); END IF;
 SELECT count(*),COALESCE(sum(reserved_bytes),0) INTO v_count,v_bytes FROM research.research_validation_replay_attempts WHERE run_id=p_run;
 SELECT v_count+count(*),v_bytes+COALESCE(sum(octet_length(outcome::text)),0) INTO v_count,v_bytes FROM research.research_validation_records r WHERE run_id=p_run AND kind='REPLAY'
 AND NOT EXISTS(SELECT 1 FROM research.research_validation_replay_attempts a WHERE a.result_id=r.id);
 IF v_count>=20 OR v_bytes+1048576>20971520 THEN RETURN jsonb_build_object('status','RUN_LIMIT'); END IF;
 INSERT INTO research.research_validation_replay_attempts(run_id,actor_id,actor_tenant_id,command_key,status,validator_revision)
 VALUES(p_run,v_actor,v_tenant,p_key,'RUNNING',p_revision) RETURNING * INTO v_attempt;
 RETURN to_jsonb(v_attempt);
END $$;
CREATE FUNCTION research.finish_validation_replay(p_id UUID,p_status TEXT,p_result UUID,p_error TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_tenant UUID; v_actor UUID; a research.research_validation_replay_attempts%ROWTYPE;
BEGIN
 v_tenant := research.validation_current_tenant(); v_actor := auth.uid();
 IF NOT research.validation_live_member(v_tenant,true) THEN RAISE EXCEPTION 'VALIDATION_ADMIN_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO a FROM research.research_validation_replay_attempts WHERE id=p_id AND actor_id=v_actor AND actor_tenant_id=v_tenant FOR UPDATE;
 IF NOT FOUND OR a.status<>'RUNNING' THEN RETURN false; END IF;
 IF p_status='COMPLETE' AND NOT EXISTS(SELECT 1 FROM research.research_validation_records r WHERE r.id=p_result AND r.kind='REPLAY' AND r.run_id=a.run_id AND r.actor_id=v_actor AND r.actor_tenant_id=v_tenant AND r.command_key=a.command_key) THEN
   RAISE EXCEPTION 'VALIDATION_REPLAY_RESULT_INVALID' USING ERRCODE='23514';
 END IF;
 UPDATE research.research_validation_replay_attempts SET status=p_status,result_id=p_result,error_code=p_error,completed_at=clock_timestamp() WHERE id=p_id;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION research.reserve_validation_replay(UUID,UUID,TEXT),research.finish_validation_replay(UUID,TEXT,UUID,TEXT) FROM PUBLIC,authenticated,wealthsail_worker;
GRANT EXECUTE ON FUNCTION research.reserve_validation_replay(UUID,UUID,TEXT),research.finish_validation_replay(UUID,TEXT,UUID,TEXT) TO wealthsail_api;
COMMENT ON TABLE research.research_validation_archive_queue IS 'Bounded immutable evidence outbox; worker may update only fenced recovery metadata. No raw API table access.';
COMMENT ON TABLE research.research_validation_replay_attempts IS 'Durable offline replay admission: 10 per actor Shanghai day, 20 per run, 20 MiB reserved per run. Same-key retries reuse the attempt.';
