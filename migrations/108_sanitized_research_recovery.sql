-- Explicit offline report publication. Original failures remain independently auditable.
CREATE TABLE research.daily_research_report_recoveries (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 run_id UUID NOT NULL REFERENCES research.daily_research_runs(run_id),
 recovered_at TIMESTAMPTZ NOT NULL,
 raw_sha256 TEXT NOT NULL CHECK(raw_sha256 ~ '^[a-f0-9]{64}$'),
 packet_digest TEXT NOT NULL CHECK(packet_digest ~ '^[a-f0-9]{32}$'),
 previous_result JSONB NOT NULL,
 previous_pipeline JSONB,
 previous_request JSONB,
 published_payload JSONB NOT NULL CHECK(jsonb_typeof(published_payload)='object'),
 UNIQUE(run_id,raw_sha256)
);
REVOKE ALL ON research.daily_research_report_recoveries FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
GRANT SELECT ON research.daily_research_report_recoveries TO wealthsail_worker;

CREATE FUNCTION research.publish_sanitized_daily_report(
 p_run UUID,p_payload JSONB,p_packet_digest TEXT,p_raw_sha256 TEXT,p_now TIMESTAMPTZ
) RETURNS TABLE(run_id UUID,published BOOLEAN,already_published BOOLEAN)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE old_result research.daily_research_results%ROWTYPE;
 old_pipeline JSONB; old_request JSONB; bound_digest TEXT;
BEGIN
 IF p_run IS NULL OR p_now IS NULL OR p_now>clock_timestamp()+interval '1 minute'
    OR p_raw_sha256 IS NULL OR p_packet_digest IS NULL
    OR p_raw_sha256 !~ '^[a-f0-9]{64}$' OR p_packet_digest !~ '^[a-f0-9]{32}$'
    OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
    OR p_payload->>'executionStatus' IS DISTINCT FROM 'COMPLETED'
    OR p_payload->'researchRecovery'->>'sourceRunId' IS DISTINCT FROM p_run::text
    OR p_payload->'researchRecovery'->>'rawSha256' IS DISTINCT FROM p_raw_sha256
    OR p_payload->'researchRecovery'->>'modelCalls' IS DISTINCT FROM '0'
 THEN RAISE EXCEPTION 'RECOVERY_PAYLOAD_INVALID' USING ERRCODE='22023'; END IF;
 SELECT t.* INTO old_result FROM research.daily_research_results t WHERE t.run_id=p_run FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'RECOVERY_RUN_NOT_FOUND' USING ERRCODE='22023'; END IF;
 SELECT md5(r.public_packet::text) INTO bound_digest FROM research.daily_research_runs r WHERE r.run_id=p_run;
 IF bound_digest IS DISTINCT FROM p_packet_digest THEN
   RAISE EXCEPTION 'RECOVERY_PACKET_MISMATCH' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM research.daily_research_report_recoveries a WHERE a.run_id=p_run AND a.raw_sha256=p_raw_sha256) THEN
   RETURN QUERY SELECT p_run,true,true; RETURN;
 END IF;
 IF old_result.status<>'FAILED' OR old_result.failure_stage IS DISTINCT FROM 'REPORT_VALIDATION'
    OR p_now<old_result.recorded_at THEN
   RAISE EXCEPTION 'RECOVERY_REPORT_FAILURE_REQUIRED' USING ERRCODE='22023'; END IF;
 SELECT to_jsonb(p) INTO old_pipeline FROM research.daily_research_pipelines p WHERE p.research_run_id=p_run FOR UPDATE;
 SELECT to_jsonb(q) INTO old_request FROM research.daily_research_refresh_requests q WHERE q.report_run_id=p_run FOR UPDATE;
 INSERT INTO research.daily_research_report_recoveries(run_id,recovered_at,raw_sha256,packet_digest,previous_result,previous_pipeline,previous_request,published_payload)
 VALUES(p_run,p_now,p_raw_sha256,p_packet_digest,to_jsonb(old_result),old_pipeline,old_request,p_payload);
 UPDATE research.daily_research_results t SET status='READY',recorded_at=p_now,payload=p_payload,error_class=NULL,
   failure_stage=NULL,failure_reason_code=NULL,failure_rule_id=NULL,failure_item_index=NULL,failure_industry_codes=NULL
 WHERE t.run_id=p_run;
 UPDATE research.daily_research_pipelines p SET state='READY',stage='PERSISTING',finished_at=p_now,updated_at=p_now,error_class=NULL
 WHERE p.research_run_id=p_run;
 UPDATE research.daily_research_refresh_requests q SET status='READY',completed_at=p_now,error_class=NULL
 WHERE q.report_run_id=p_run;
 RETURN QUERY SELECT p_run,true,false;
END $$;
REVOKE ALL ON FUNCTION research.publish_sanitized_daily_report(UUID,JSONB,TEXT,TEXT,TIMESTAMPTZ) FROM PUBLIC,authenticated,wealthsail_api;
GRANT EXECUTE ON FUNCTION research.publish_sanitized_daily_report(UUID,JSONB,TEXT,TEXT,TIMESTAMPTZ) TO wealthsail_worker;
COMMENT ON TABLE research.daily_research_report_recoveries IS 'Original failed result and pipeline snapshots, plus zero-model replay publication. Research run/cost barriers are never modified.';
