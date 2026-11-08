-- Queue deadlines never release model guards or reuse immutable execution slots.
ALTER TABLE research.daily_research_refresh_requests
 DROP CONSTRAINT daily_research_refresh_requests_status_check;

-- Replace the original multi-column state-shape CHECK without relying on an
-- auto-generated suffix (the other timestamp and READY-proof checks remain).
DO $$
DECLARE c RECORD;
BEGIN
 FOR c IN SELECT conname FROM pg_constraint
   WHERE conrelid='research.daily_research_refresh_requests'::regclass AND contype='c'
     AND conkey @> ARRAY[
       (SELECT attnum FROM pg_attribute WHERE attrelid='research.daily_research_refresh_requests'::regclass AND attname='status'),
       (SELECT attnum FROM pg_attribute WHERE attrelid='research.daily_research_refresh_requests'::regclass AND attname='started_at'),
       (SELECT attnum FROM pg_attribute WHERE attrelid='research.daily_research_refresh_requests'::regclass AND attname='completed_at')
     ]::smallint[]
 LOOP EXECUTE format('ALTER TABLE research.daily_research_refresh_requests DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;

ALTER TABLE research.daily_research_refresh_requests
 ADD CONSTRAINT research_refresh_status CHECK(status IN ('QUEUED','RUNNING','READY','FAILED','UNCERTAIN','CANCELLED','EXPIRED')),
 ADD CONSTRAINT research_refresh_state_shape CHECK(
   (status='QUEUED' AND started_at IS NULL AND completed_at IS NULL) OR
   (status='RUNNING' AND started_at IS NOT NULL AND completed_at IS NULL) OR
   (status IN ('READY','FAILED','UNCERTAIN') AND completed_at IS NOT NULL) OR
   (status IN ('CANCELLED','EXPIRED') AND started_at IS NULL AND completed_at IS NOT NULL
     AND batch_id IS NULL AND report_run_id IS NULL AND error_class IS NOT NULL)),
 ADD COLUMN execution_stage TEXT CHECK(execution_stage IN ('COLLECTING','WAITING_MODEL_SLOT','MODEL_RUNNING','VERIFYING_OUTPUT','REPORT_VALIDATION','PERSISTING')),
 ADD COLUMN stage_updated_at TIMESTAMPTZ,
 ADD CONSTRAINT research_refresh_stage_shape CHECK(
   (execution_stage IS NULL AND stage_updated_at IS NULL) OR
   (execution_stage IS NOT NULL AND stage_updated_at IS NOT NULL AND started_at IS NOT NULL AND stage_updated_at>=started_at));
GRANT UPDATE(execution_stage,stage_updated_at) ON research.daily_research_refresh_requests TO wealthsail_worker;

-- The API has no general UPDATE grant, including no ability to fake progress.
-- These owner policies serve only the migration-owned SECURITY DEFINER helpers.
CREATE POLICY research_refresh_control_owner ON research.daily_research_refresh_requests
 TO CURRENT_USER USING(true) WITH CHECK(true);
CREATE POLICY research_guard_control_owner ON research.daily_research_model_guards
 TO CURRENT_USER USING(true) WITH CHECK(true);

CREATE FUNCTION research.research_refresh_control_actor(p_tenant UUID,p_user UUID,p_owner BOOLEAN DEFAULT false)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p_tenant IS NOT NULL AND p_user IS NOT NULL
 AND (
   (current_setting('role',true)='wealthsail_worker' AND pg_has_role(session_user,'wealthsail_worker','MEMBER')) OR
   (auth.uid()=p_user AND (NULLIF(current_setting('request.jwt.claims',true),'')::jsonb->>'tenant_id')=p_tenant::text)
 ) AND EXISTS(
   SELECT 1 FROM identity.tenant_members m JOIN identity.tenants t ON t.id=m.tenant_id
   WHERE m.tenant_id=p_tenant AND m.user_id=p_user AND m.status='ACTIVE' AND t.status='ACTIVE'
     AND (m.role='OWNER' OR (NOT p_owner AND m.role='ADMIN')))
$$;
REVOKE ALL ON FUNCTION research.research_refresh_control_actor(UUID,UUID,BOOLEAN)
 FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;

CREATE FUNCTION research.expire_queued_research_refresh(p_tenant UUID,p_user UUID,p_now TIMESTAMPTZ)
RETURNS INTEGER LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE n INTEGER; v_now TIMESTAMPTZ; cutoff TIMESTAMPTZ;
BEGIN
 IF p_now IS NULL OR NOT COALESCE(research.research_refresh_control_actor(p_tenant,p_user),false) THEN
   RAISE EXCEPTION 'RESEARCH_QUEUE_OPERATOR_REQUIRED' USING ERRCODE='42501';
 END IF;
 -- p_now is a visibility hint, never authority to expire a live request early.
 v_now:=clock_timestamp();
 SELECT max(queue_cutoff_at) INTO cutoff FROM research.local_research_recovery_audit;
 UPDATE research.daily_research_refresh_requests SET status='EXPIRED',completed_at=v_now,
   error_class=CASE WHEN requested_at<=v_now-interval '5 minutes' THEN 'QueueWaitExpired' ELSE 'QueueInvalidatedByMaintenance' END
 WHERE tenant_id=p_tenant AND user_id=p_user AND status='QUEUED' AND started_at IS NULL
   AND batch_id IS NULL AND report_run_id IS NULL AND (requested_at<=v_now-interval '5 minutes' OR requested_at<=cutoff);
 GET DIAGNOSTICS n=ROW_COUNT;
 RETURN n;
END $$;

CREATE FUNCTION research.cancel_queued_research_refresh(p_tenant UUID,p_user UUID,p_request UUID,p_now TIMESTAMPTZ)
RETURNS SETOF research.daily_research_refresh_requests
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q research.daily_research_refresh_requests%ROWTYPE; v_now TIMESTAMPTZ;
BEGIN
 IF p_now IS NULL OR NOT COALESCE(research.research_refresh_control_actor(p_tenant,p_user,true),false) THEN
   RAISE EXCEPTION 'RESEARCH_QUEUE_OWNER_REQUIRED' USING ERRCODE='42501';
 END IF;
 v_now:=clock_timestamp();
 PERFORM research.expire_queued_research_refresh(p_tenant,p_user,p_now);
 SELECT * INTO q FROM research.daily_research_refresh_requests
 WHERE id=p_request AND tenant_id=p_tenant AND user_id=p_user AND requested_at<=v_now FOR UPDATE;
 IF NOT FOUND THEN RETURN; END IF;
 v_now:=clock_timestamp();
 IF q.status='QUEUED' AND q.started_at IS NULL AND q.batch_id IS NULL AND q.report_run_id IS NULL THEN
   UPDATE research.daily_research_refresh_requests
   SET status=CASE WHEN requested_at<=v_now-interval '5 minutes' THEN 'EXPIRED' ELSE 'CANCELLED' END,
     completed_at=v_now,error_class=CASE WHEN requested_at<=v_now-interval '5 minutes' THEN 'QueueWaitExpired' ELSE 'CancelledBeforeStart' END
   WHERE id=q.id AND status='QUEUED' AND started_at IS NULL AND batch_id IS NULL AND report_run_id IS NULL
   RETURNING * INTO q;
 END IF;
 RETURN NEXT q;
END $$;

CREATE FUNCTION research.research_refresh_queue_observation(p_tenant UUID,p_user UUID,p_request UUID,p_now TIMESTAMPTZ)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q research.daily_research_refresh_requests%ROWTYPE; reason TEXT; since_at TIMESTAMPTZ; position BIGINT; can_cancel BOOLEAN; exit_state TEXT; checked_at TEXT;
BEGIN
 IF p_now IS NULL OR NOT COALESCE(research.research_refresh_control_actor(p_tenant,p_user),false) THEN
   RAISE EXCEPTION 'RESEARCH_QUEUE_OPERATOR_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO q FROM research.daily_research_refresh_requests
 WHERE id=p_request AND tenant_id=p_tenant AND user_id=p_user AND requested_at<=p_now;
 IF NOT FOUND OR q.status<>'QUEUED' OR q.started_at IS NOT NULL OR q.requested_at<=p_now-interval '5 minutes' THEN RETURN NULL; END IF;
 SELECT acquired_at,to_jsonb(g)->'exit_evidence'->>'evidenceState',to_jsonb(g)->'exit_evidence'->>'checkedAt'
 INTO since_at,exit_state,checked_at FROM research.daily_research_model_guards g
 WHERE released_at IS NULL AND acquired_at<=p_now ORDER BY acquired_at LIMIT 1;
 IF since_at IS NOT NULL THEN reason:='MODEL_GUARD_ACTIVE';
 ELSE
   SELECT min(started_at) INTO since_at FROM research.daily_research_refresh_requests WHERE status='RUNNING' AND started_at<=p_now;
   reason:=CASE WHEN since_at IS NOT NULL THEN 'REQUEST_RUNNING' ELSE 'WAITING_WORKER' END;
 END IF;
 SELECT count(*) INTO position FROM research.daily_research_refresh_requests
 WHERE status='QUEUED' AND started_at IS NULL AND requested_at>p_now-interval '5 minutes'
   AND (requested_at,id)<=(q.requested_at,q.id);
 SELECT COALESCE(research.research_refresh_control_actor(p_tenant,p_user,true),false) INTO can_cancel;
 RETURN jsonb_build_object('blockedReasonCode',reason,'blockedSince',since_at,'queuePosition',position,'canCancel',can_cancel,
   'modelExitEvidenceState',CASE WHEN exit_state IN ('LOCAL_RUNNING','LOCAL_EXIT_CONFIRMED','LOCAL_EXIT_UNCONFIRMED','LOCAL_EVIDENCE_NOT_RETAINED','LOCAL_PLATFORM_UNSUPPORTED') THEN exit_state ELSE NULL END,
   'modelExitEvidenceCheckedAt',checked_at);
END $$;

REVOKE ALL ON FUNCTION research.expire_queued_research_refresh(UUID,UUID,TIMESTAMPTZ),
 research.cancel_queued_research_refresh(UUID,UUID,UUID,TIMESTAMPTZ),
 research.research_refresh_queue_observation(UUID,UUID,UUID,TIMESTAMPTZ)
 FROM PUBLIC,authenticated;
GRANT EXECUTE ON FUNCTION research.expire_queued_research_refresh(UUID,UUID,TIMESTAMPTZ),
 research.cancel_queued_research_refresh(UUID,UUID,UUID,TIMESTAMPTZ),
 research.research_refresh_queue_observation(UUID,UUID,UUID,TIMESTAMPTZ)
 TO wealthsail_api,wealthsail_worker;
