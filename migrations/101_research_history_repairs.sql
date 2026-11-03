-- Independent history-only derivations. Nothing updates a formal research result.
CREATE TABLE research.research_history_repair_attempts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 source_run_id UUID NOT NULL REFERENCES research.daily_research_runs(run_id),
 tenant_id UUID NOT NULL REFERENCES identity.tenants(id),
 actor_id UUID NOT NULL REFERENCES identity.profiles(id),
 command_key UUID NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETE','FAILED','TIMED_OUT','CANCELLED')),
 reserved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 completed_at TIMESTAMPTZ,
 reserved_bytes INTEGER NOT NULL DEFAULT 4194304 CHECK(reserved_bytes=4194304),
 result_id UUID,
 error_code TEXT CHECK(error_code IN ('TIMEOUT','CANCELLED','INVALID_OUTPUT','VALIDATOR_FAILED','MATERIALS_INVALID','OUTPUT_LIMIT')),
 validator_revision TEXT CHECK(validator_revision ~ '^[a-f0-9]{64}$'),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 UNIQUE(tenant_id,actor_id,command_key),
 CHECK((status='RUNNING' AND completed_at IS NULL AND result_id IS NULL AND error_code IS NULL) OR
       (status='COMPLETE' AND completed_at IS NOT NULL AND result_id IS NOT NULL AND error_code IS NULL) OR
       (status IN ('FAILED','TIMED_OUT','CANCELLED') AND completed_at IS NOT NULL AND result_id IS NULL AND error_code IS NOT NULL))
);
CREATE TABLE research.research_history_repair_derivations (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 source_run_id UUID NOT NULL REFERENCES research.daily_research_runs(run_id),
 initial_record_id UUID NOT NULL REFERENCES research.research_validation_records(id),
 source_tenant_id UUID REFERENCES identity.tenants(id),
 tenant_id UUID NOT NULL REFERENCES identity.tenants(id),
 creator_id UUID NOT NULL REFERENCES identity.profiles(id),
 command_key UUID NOT NULL,
 attempt_id UUID NOT NULL UNIQUE REFERENCES research.research_history_repair_attempts(id),
 validated_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 candidate_digest TEXT NOT NULL CHECK(candidate_digest ~ '^[a-f0-9]{64}$'),
 source_digests JSONB NOT NULL CHECK(jsonb_typeof(source_digests)='object' AND octet_length(source_digests::text)<=1024),
 private_result JSONB NOT NULL CHECK(jsonb_typeof(private_result)='object'),
 safe_summary JSONB NOT NULL CHECK(jsonb_typeof(safe_summary)='object' AND octet_length(safe_summary::text)<=1048576),
 FOREIGN KEY(tenant_id,creator_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 UNIQUE(tenant_id,creator_id,command_key),
 CHECK(source_tenant_id IS NULL OR source_tenant_id=tenant_id),
 CHECK(octet_length(private_result::text)+octet_length(safe_summary::text)+octet_length(source_digests::text)<=4194304)
);
ALTER TABLE research.research_history_repair_attempts ADD FOREIGN KEY(result_id) REFERENCES research.research_history_repair_derivations(id);
CREATE TABLE research.research_history_repair_events (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 derivation_id UUID NOT NULL REFERENCES research.research_history_repair_derivations(id),
 tenant_id UUID NOT NULL REFERENCES identity.tenants(id),
 actor_id UUID NOT NULL REFERENCES identity.profiles(id),
 command_key UUID NOT NULL,
 event_type TEXT NOT NULL CHECK(event_type IN ('APPROVE','REJECT','PUBLISH')),
 candidate_digest TEXT NOT NULL CHECK(candidate_digest ~ '^[a-f0-9]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 UNIQUE(tenant_id,actor_id,command_key)
);
CREATE UNIQUE INDEX history_repair_one_review ON research.research_history_repair_events(derivation_id) WHERE event_type IN ('APPROVE','REJECT');
CREATE UNIQUE INDEX history_repair_one_publication ON research.research_history_repair_events(derivation_id) WHERE event_type='PUBLISH';
CREATE INDEX history_repair_source_history ON research.research_history_repair_derivations(tenant_id,source_run_id,created_at DESC,id);
CREATE INDEX history_repair_actor_budget ON research.research_history_repair_attempts(actor_id,reserved_at);
CREATE INDEX history_repair_run_budget ON research.research_history_repair_attempts(source_run_id);
ALTER TABLE research.research_history_repair_derivations ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.research_history_repair_derivations FORCE ROW LEVEL SECURITY;
ALTER TABLE research.research_history_repair_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.research_history_repair_events FORCE ROW LEVEL SECURITY;
ALTER TABLE research.research_history_repair_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.research_history_repair_attempts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON research.research_history_repair_derivations,research.research_history_repair_events,research.research_history_repair_attempts FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
CREATE POLICY history_repair_owner_derivation ON research.research_history_repair_derivations TO CURRENT_USER USING(true) WITH CHECK(true);
CREATE POLICY history_repair_owner_event ON research.research_history_repair_events TO CURRENT_USER USING(true) WITH CHECK(true);
CREATE POLICY history_repair_owner_attempt ON research.research_history_repair_attempts TO CURRENT_USER USING(true) WITH CHECK(true);

CREATE FUNCTION research.history_repair_source_admin(p_run UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result JSONB; v_tenant UUID;
BEGIN
 v_tenant := research.validation_current_tenant();
 IF NOT research.validation_live_member(v_tenant,true) THEN RAISE EXCEPTION 'HISTORY_REPAIR_ADMIN_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('sourceRunId',r.run_id,'initialRecordId',i.id,'sourceTenantId',q.tenant_id,
   'validatedAt',a.validated_at,'dispatchedAt',r.dispatched_at,'originalOutcome',i.outcome,
   'sourceDigests',jsonb_build_object('PACKET',a.packet->>'sha256','RAW_MODEL',a.raw_output->>'sha256','SOURCE_CHECKS',a.source_checks->>'sha256')) INTO result
 FROM research.daily_research_runs r JOIN research.daily_research_results x USING(run_id)
 JOIN research.research_validation_records i ON i.run_id=r.run_id AND i.kind='INITIAL'
 JOIN research.research_validation_artifacts a ON a.run_id=r.run_id
 LEFT JOIN research.research_validation_archive_queue z ON z.run_id=r.run_id
 LEFT JOIN research.daily_research_refresh_requests q ON q.id=r.debug_request_id AND q.request_mode=r.run_mode
 WHERE r.run_id=p_run AND x.status='FAILED' AND x.recorded_at<=clock_timestamp() AND r.dispatched_at<=clock_timestamp()
   AND (r.run_mode='SCHEDULED' OR q.tenant_id=v_tenant)
   AND (i.tenant_id IS NULL OR i.tenant_id=v_tenant) AND (a.tenant_id IS NULL OR a.tenant_id=v_tenant)
   AND (z.run_id IS NULL OR z.status='ARCHIVED') AND i.validated_at=a.validated_at
   AND a.validated_at>=r.dispatched_at AND a.validated_at<=clock_timestamp()
   AND i.outcome->>'researchId'=r.run_id::text;
 RETURN result;
END $$;

-- Same actor/run lock namespace as ordinary replay, plus two bounded repair slots.
CREATE FUNCTION research.try_history_repair_locks(p_run UUID) RETURNS BOOLEAN
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT research.validation_live_member(research.validation_current_tenant(),true) THEN RAISE EXCEPTION 'HISTORY_REPAIR_ADMIN_REQUIRED' USING ERRCODE='42501'; END IF;
 IF NOT pg_try_advisory_xact_lock(hashtextextended('acceptance:actor:'||auth.uid()::text,0)) OR NOT pg_try_advisory_xact_lock(hashtextextended('acceptance:run:'||p_run::text,0)) THEN RETURN false; END IF;
 RETURN pg_try_advisory_xact_lock(hashtextextended('acceptance:repair:global:0',0)) OR pg_try_advisory_xact_lock(hashtextextended('acceptance:repair:global:1',0));
END $$;

CREATE FUNCTION research.validation_combined_budget(p_actor UUID,p_run UUID) RETURNS TABLE(actor_count BIGINT,run_count BIGINT,run_bytes BIGINT)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH attempts AS (
   SELECT actor_id,run_id,reserved_at,reserved_bytes::bigint AS bytes FROM research.research_validation_replay_attempts
   UNION ALL SELECT actor_id,source_run_id,reserved_at,reserved_bytes::bigint FROM research.research_history_repair_attempts
   UNION ALL SELECT r.actor_id,r.run_id,r.recorded_at,octet_length(r.outcome::text)::bigint FROM research.research_validation_records r
      WHERE r.kind='REPLAY' AND NOT EXISTS(SELECT 1 FROM research.research_validation_replay_attempts a WHERE a.result_id=r.id))
 SELECT count(*) FILTER(WHERE actor_id=p_actor AND (reserved_at AT TIME ZONE 'Asia/Shanghai')::date=(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date),
 count(*) FILTER(WHERE run_id=p_run),COALESCE(sum(bytes) FILTER(WHERE run_id=p_run),0) FROM attempts
$$;
REVOKE ALL ON FUNCTION research.validation_combined_budget(UUID,UUID) FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;

CREATE OR REPLACE FUNCTION research.reserve_validation_replay(p_run UUID,p_key UUID,p_revision TEXT) RETURNS JSONB
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_tenant UUID; v_actor UUID; a research.research_validation_replay_attempts%ROWTYPE; v_scope UUID; v_mode TEXT; b RECORD;
BEGIN
 v_tenant:=research.validation_current_tenant(); v_actor:=auth.uid();
 IF NOT research.validation_live_member(v_tenant,true) THEN RAISE EXCEPTION 'VALIDATION_ADMIN_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO a FROM research.research_validation_replay_attempts WHERE actor_tenant_id=v_tenant AND actor_id=v_actor AND command_key=p_key;
 IF FOUND THEN
   IF a.run_id<>p_run THEN RETURN jsonb_build_object('status','CONFLICT'); END IF;
   RETURN to_jsonb(a);
 END IF;
 IF NOT pg_try_advisory_xact_lock(hashtextextended('acceptance:actor:'||v_actor::text,0)) OR NOT pg_try_advisory_xact_lock(hashtextextended('acceptance:run:'||p_run::text,0)) THEN RETURN jsonb_build_object('status','BUSY'); END IF;
 SELECT q.tenant_id,r.run_mode INTO v_scope,v_mode FROM research.daily_research_runs r LEFT JOIN research.daily_research_refresh_requests q ON q.id=r.debug_request_id WHERE r.run_id=p_run;
 IF NOT FOUND OR (v_scope IS NOT NULL AND v_scope<>v_tenant) OR (v_mode<>'SCHEDULED' AND v_scope IS NULL) THEN RETURN jsonb_build_object('status','NOT_FOUND'); END IF;
 SELECT * INTO b FROM research.validation_combined_budget(v_actor,p_run);
 IF b.actor_count>=10 THEN RETURN jsonb_build_object('status','ACTOR_LIMIT'); END IF;
 IF b.run_count>=20 OR b.run_bytes+1048576>20971520 THEN RETURN jsonb_build_object('status','RUN_LIMIT'); END IF;
 INSERT INTO research.research_validation_replay_attempts(run_id,actor_id,actor_tenant_id,command_key,status,validator_revision)
 VALUES(p_run,v_actor,v_tenant,p_key,'RUNNING',p_revision) RETURNING * INTO a;
 RETURN to_jsonb(a);
END $$;

CREATE FUNCTION research.reserve_history_repair(p_run UUID,p_key UUID,p_revision TEXT) RETURNS JSONB
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a research.research_history_repair_attempts%ROWTYPE; b RECORD; v_tenant UUID; v_actor UUID;
BEGIN
 v_tenant:=research.validation_current_tenant(); v_actor:=auth.uid();
 IF NOT research.validation_live_member(v_tenant,true) THEN RAISE EXCEPTION 'HISTORY_REPAIR_ADMIN_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO a FROM research.research_history_repair_attempts WHERE tenant_id=v_tenant AND actor_id=v_actor AND command_key=p_key;
 IF FOUND THEN
   IF a.source_run_id<>p_run THEN RETURN jsonb_build_object('status','CONFLICT'); END IF;
   RETURN to_jsonb(a);
 END IF;
 IF research.history_repair_source_admin(p_run) IS NULL THEN RETURN jsonb_build_object('status','NOT_FOUND'); END IF;
 IF NOT research.try_history_repair_locks(p_run) THEN RETURN jsonb_build_object('status','BUSY'); END IF;
 SELECT * INTO b FROM research.validation_combined_budget(v_actor,p_run);
 IF b.actor_count>=10 THEN RETURN jsonb_build_object('status','ACTOR_LIMIT'); END IF;
 IF b.run_count>=20 OR b.run_bytes+4194304>20971520 THEN RETURN jsonb_build_object('status','RUN_LIMIT'); END IF;
 INSERT INTO research.research_history_repair_attempts(source_run_id,tenant_id,actor_id,command_key,status,validator_revision)
 VALUES(p_run,v_tenant,v_actor,p_key,'RUNNING',p_revision) RETURNING * INTO a;
 RETURN to_jsonb(a)||jsonb_build_object('newAttempt',true);
END $$;

CREATE FUNCTION research.finish_history_repair_failure(p_id UUID,p_error TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT research.validation_live_member(research.validation_current_tenant(),true) THEN RAISE EXCEPTION 'HISTORY_REPAIR_ADMIN_REQUIRED' USING ERRCODE='42501'; END IF;
 UPDATE research.research_history_repair_attempts SET status=CASE WHEN p_error='TIMEOUT' THEN 'TIMED_OUT' WHEN p_error='CANCELLED' THEN 'CANCELLED' ELSE 'FAILED' END,
   completed_at=clock_timestamp(),error_code=p_error WHERE id=p_id AND tenant_id=research.validation_current_tenant() AND actor_id=auth.uid() AND status='RUNNING';
 RETURN FOUND;
END $$;

CREATE FUNCTION research.save_history_repair(p_attempt UUID,p_result JSONB,p_summary JSONB,p_digest TEXT) RETURNS UUID
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a research.research_history_repair_attempts%ROWTYPE; s JSONB; v_id UUID;
BEGIN
 IF NOT research.validation_live_member(research.validation_current_tenant(),true) THEN RAISE EXCEPTION 'HISTORY_REPAIR_ADMIN_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO a FROM research.research_history_repair_attempts WHERE id=p_attempt AND tenant_id=research.validation_current_tenant() AND actor_id=auth.uid() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'HISTORY_REPAIR_ATTEMPT_INVALID' USING ERRCODE='23514'; END IF;
 IF a.status='COMPLETE' THEN RETURN a.result_id; END IF;
 IF a.status<>'RUNNING' THEN RAISE EXCEPTION 'HISTORY_REPAIR_ATTEMPT_INVALID' USING ERRCODE='23514'; END IF;
 s:=research.history_repair_source_admin(a.source_run_id);
 IF s IS NULL OR (p_result->>'validatedAt')::timestamptz<>(s->>'validatedAt')::timestamptz OR p_summary->>'sourceRunId'<>a.source_run_id::text THEN RAISE EXCEPTION 'HISTORY_REPAIR_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
 INSERT INTO research.research_history_repair_derivations(source_run_id,initial_record_id,source_tenant_id,tenant_id,creator_id,command_key,attempt_id,validated_at,candidate_digest,source_digests,private_result,safe_summary)
 VALUES(a.source_run_id,(s->>'initialRecordId')::uuid,(s->>'sourceTenantId')::uuid,a.tenant_id,a.actor_id,a.command_key,a.id,(s->>'validatedAt')::timestamptz,p_digest,s->'sourceDigests',p_result,p_summary) RETURNING id INTO v_id;
 UPDATE research.research_history_repair_attempts SET status='COMPLETE',result_id=v_id,completed_at=clock_timestamp() WHERE id=a.id;
 RETURN v_id;
END $$;

CREATE FUNCTION research.read_history_repair(p_id UUID,p_admin BOOLEAN DEFAULT false) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d research.research_history_repair_derivations%ROWTYPE; v_state TEXT; v_review UUID; v_tenant UUID; result JSONB;
BEGIN
 v_tenant:=research.validation_current_tenant();
 IF NOT research.validation_live_member(v_tenant,p_admin) THEN RAISE EXCEPTION 'HISTORY_REPAIR_MEMBERSHIP_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM research.research_history_repair_derivations WHERE id=p_id AND tenant_id=v_tenant;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT actor_id INTO v_review FROM research.research_history_repair_events WHERE derivation_id=d.id AND event_type='APPROVE';
 v_state:=CASE WHEN EXISTS(SELECT 1 FROM research.research_history_repair_events WHERE derivation_id=d.id AND event_type='PUBLISH') THEN 'PUBLISHED'
   WHEN v_review IS NOT NULL THEN 'APPROVED' WHEN EXISTS(SELECT 1 FROM research.research_history_repair_events WHERE derivation_id=d.id AND event_type='REJECT') THEN 'REJECTED' ELSE 'DRAFT' END;
 IF NOT p_admin AND v_state<>'PUBLISHED' THEN RETURN NULL; END IF;
 result:=jsonb_build_object('id',d.id,'sourceRunId',d.source_run_id,'state',v_state,'createdAt',d.created_at,'validatedAt',d.validated_at,'summary',d.safe_summary,
   'canInspectRepairDetails',p_admin,'canGenerate',p_admin,'canReview',p_admin AND d.creator_id<>auth.uid() AND v_state='DRAFT' AND d.private_result->>'strictPass'='true' AND d.private_result->>'historicalPublishAllowed'='true' AND d.private_result->>'safetyBlocked'='false' AND COALESCE(jsonb_array_length(d.private_result->'hardBlockers'),0)=0,
   'canReject',p_admin AND d.creator_id<>auth.uid() AND v_state='DRAFT',
   'canPublish',p_admin AND v_state='APPROVED' AND EXISTS(SELECT 1 FROM identity.tenant_members m WHERE m.tenant_id=v_tenant AND m.user_id=v_review AND m.role IN ('OWNER','ADMIN') AND m.status='ACTIVE'));
 IF p_admin THEN result:=result||jsonb_build_object('creatorId',d.creator_id,'reviewerId',v_review,'candidateDigest',d.candidate_digest,'sourceDigests',d.source_digests,'privateResult',d.private_result); END IF;
 RETURN result;
END $$;
CREATE FUNCTION research.list_history_repairs(p_run UUID,p_admin BOOLEAN DEFAULT false,p_limit INTEGER DEFAULT 20) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result JSONB;
BEGIN
 IF NOT research.validation_live_member(research.validation_current_tenant(),p_admin) THEN RAISE EXCEPTION 'HISTORY_REPAIR_MEMBERSHIP_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(jsonb_agg(value ORDER BY created_at DESC,id), '[]'::jsonb) INTO result FROM (
 SELECT d.id,d.created_at,research.read_history_repair(d.id,p_admin) AS value FROM research.research_history_repair_derivations d
 WHERE d.tenant_id=research.validation_current_tenant() AND (p_run IS NULL OR d.source_run_id=p_run)
   AND (p_admin OR EXISTS(SELECT 1 FROM research.research_history_repair_events e WHERE e.derivation_id=d.id AND e.event_type='PUBLISH'))
 ORDER BY d.created_at DESC,d.id LIMIT GREATEST(1,LEAST(p_limit,20))) bounded;
 RETURN result;
END $$;

CREATE FUNCTION research.record_history_repair_event(p_id UUID,p_key UUID,p_kind TEXT) RETURNS JSONB
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d research.research_history_repair_derivations%ROWTYPE; e research.research_history_repair_events%ROWTYPE; review_actor UUID; v_tenant UUID;
BEGIN
 v_tenant:=research.validation_current_tenant();
 IF NOT research.validation_live_member(v_tenant,true) THEN RAISE EXCEPTION 'HISTORY_REPAIR_ADMIN_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO e FROM research.research_history_repair_events WHERE tenant_id=v_tenant AND actor_id=auth.uid() AND command_key=p_key;
 IF FOUND THEN
   IF e.derivation_id<>p_id OR e.event_type<>p_kind THEN RETURN jsonb_build_object('status','CONFLICT'); END IF;
   RETURN jsonb_build_object('status','COMPLETE');
 END IF;
 SELECT * INTO d FROM research.research_history_repair_derivations WHERE id=p_id AND tenant_id=v_tenant FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','NOT_FOUND'); END IF;
 IF research.history_repair_source_admin(d.source_run_id) IS NULL THEN RAISE EXCEPTION 'HISTORY_REPAIR_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
 IF p_kind IN ('APPROVE','REJECT') THEN
   IF d.creator_id=auth.uid() THEN RAISE EXCEPTION 'HISTORY_REPAIR_INDEPENDENT_REVIEW_REQUIRED' USING ERRCODE='42501'; END IF;
   IF EXISTS(SELECT 1 FROM research.research_history_repair_events WHERE derivation_id=p_id) THEN RAISE EXCEPTION 'HISTORY_REPAIR_STATE_INVALID' USING ERRCODE='23514'; END IF;
 ELSIF p_kind='PUBLISH' THEN
   SELECT actor_id INTO review_actor FROM research.research_history_repair_events WHERE derivation_id=p_id AND event_type='APPROVE' AND candidate_digest=d.candidate_digest;
   IF review_actor IS NULL OR review_actor=d.creator_id OR NOT EXISTS(SELECT 1 FROM identity.tenant_members WHERE tenant_id=v_tenant AND user_id=review_actor AND role IN ('OWNER','ADMIN') AND status='ACTIVE') OR EXISTS(SELECT 1 FROM research.research_history_repair_events WHERE derivation_id=p_id AND event_type IN ('REJECT','PUBLISH')) THEN RAISE EXCEPTION 'HISTORY_REPAIR_REVIEW_REQUIRED' USING ERRCODE='23514'; END IF;
 ELSE RAISE EXCEPTION 'HISTORY_REPAIR_EVENT_INVALID' USING ERRCODE='23514'; END IF;
 IF p_kind IN ('APPROVE','PUBLISH') AND (d.private_result->>'strictPass' IS DISTINCT FROM 'true' OR d.private_result->>'historicalPublishAllowed' IS DISTINCT FROM 'true' OR d.private_result->>'safetyBlocked' IS DISTINCT FROM 'false' OR jsonb_typeof(d.private_result->'formalReport') IS DISTINCT FROM 'object' OR COALESCE(jsonb_array_length(d.private_result->'hardBlockers'),0)>0) THEN RAISE EXCEPTION 'HISTORY_REPAIR_SAFETY_BLOCKED' USING ERRCODE='23514'; END IF;
 INSERT INTO research.research_history_repair_events(derivation_id,tenant_id,actor_id,command_key,event_type,candidate_digest) VALUES(p_id,v_tenant,auth.uid(),p_key,p_kind,d.candidate_digest);
 RETURN jsonb_build_object('status','COMPLETE');
END $$;

REVOKE ALL ON FUNCTION research.history_repair_source_admin(UUID),research.try_history_repair_locks(UUID),research.reserve_history_repair(UUID,UUID,TEXT),research.finish_history_repair_failure(UUID,TEXT),research.save_history_repair(UUID,JSONB,JSONB,TEXT),research.read_history_repair(UUID,BOOLEAN),research.list_history_repairs(UUID,BOOLEAN,INTEGER),research.record_history_repair_event(UUID,UUID,TEXT) FROM PUBLIC,authenticated,wealthsail_worker;
GRANT EXECUTE ON FUNCTION research.history_repair_source_admin(UUID),research.try_history_repair_locks(UUID),research.reserve_history_repair(UUID,UUID,TEXT),research.finish_history_repair_failure(UUID,TEXT),research.save_history_repair(UUID,JSONB,JSONB,TEXT),research.read_history_repair(UUID,BOOLEAN),research.list_history_repairs(UUID,BOOLEAN,INTEGER),research.record_history_repair_event(UUID,UUID,TEXT) TO wealthsail_api;
COMMENT ON TABLE research.research_history_repair_derivations IS 'Immutable tenant-local historical observations. Never a READY result, study origin, risk feed or notification source.';
COMMENT ON TABLE research.research_history_repair_events IS 'Append-only independent review and explicit history-only publication; creator cannot review own candidate.';
