-- Frozen topic observations are independent of formal READY reports and INITIAL receipts.
CREATE TABLE research.research_topic_snapshots (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 run_id UUID NOT NULL UNIQUE REFERENCES research.daily_research_runs(run_id),
 tenant_id UUID REFERENCES identity.tenants(id),
 initial_record_id UUID NOT NULL REFERENCES research.research_validation_records(id),
 validated_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 total_count INTEGER NOT NULL CHECK(total_count BETWEEN 0 AND 72),
 content_digest TEXT NOT NULL CHECK(content_digest ~ '^[a-f0-9]{64}$'),
 material_hashes JSONB NOT NULL CHECK(jsonb_typeof(material_hashes)='object' AND octet_length(material_hashes::text)<=1024),
 metadata JSONB NOT NULL CHECK(jsonb_typeof(metadata)='object' AND octet_length(metadata::text)<=1048576)
);
CREATE TABLE research.research_topic_entries (
 snapshot_id UUID NOT NULL REFERENCES research.research_topic_snapshots(id),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 72),
 topic_id TEXT NOT NULL CHECK(topic_id ~ '^topic:[a-f0-9]{32}$'),
 status TEXT NOT NULL CHECK(status IN ('FORMAL','OBSERVATION_ONLY','PENDING_REVIEW','QUARANTINED')),
 investment_eligible BOOLEAN NOT NULL DEFAULT false,
 body JSONB NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=16384),
 PRIMARY KEY(snapshot_id,ordinal), UNIQUE(snapshot_id,topic_id),
 CHECK(NOT investment_eligible OR status='FORMAL')
);
CREATE INDEX research_topic_scope_history ON research.research_topic_snapshots(tenant_id,created_at DESC,id DESC);
CREATE INDEX research_topic_status_page ON research.research_topic_entries(snapshot_id,status,ordinal);
ALTER TABLE research.research_topic_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.research_topic_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE research.research_topic_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.research_topic_entries FORCE ROW LEVEL SECURITY;
REVOKE ALL ON research.research_topic_snapshots,research.research_topic_entries FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
GRANT SELECT ON research.research_topic_snapshots,research.research_topic_entries TO wealthsail_api,wealthsail_worker;
CREATE POLICY topic_snapshot_owner ON research.research_topic_snapshots TO CURRENT_USER USING(true) WITH CHECK(true);
CREATE POLICY topic_entry_owner ON research.research_topic_entries TO CURRENT_USER USING(true) WITH CHECK(true);
CREATE POLICY topic_snapshot_worker ON research.research_topic_snapshots FOR SELECT TO wealthsail_worker USING(true);
CREATE POLICY topic_entry_worker ON research.research_topic_entries FOR SELECT TO wealthsail_worker USING(true);
CREATE POLICY topic_snapshot_member ON research.research_topic_snapshots FOR SELECT TO wealthsail_api USING(
 research.validation_live_member(research.validation_current_tenant(),false) AND (tenant_id IS NULL OR tenant_id=research.validation_current_tenant()));
CREATE POLICY topic_entry_member ON research.research_topic_entries FOR SELECT TO wealthsail_api USING(EXISTS(
 SELECT 1 FROM research.research_topic_snapshots s WHERE s.id=snapshot_id));

CREATE FUNCTION research.save_topic_registry(p_run UUID,p_registry JSONB) RETURNS UUID
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a research.research_validation_artifacts%ROWTYPE; i research.research_validation_records%ROWTYPE;
 v_scope UUID; v_mode TEXT; v_dispatch TIMESTAMPTZ; v_id UUID; v_digest TEXT; row JSONB; n INTEGER:=0; existing TEXT; selected JSONB; descriptor JSONB; queued JSONB;
BEGIN
 IF jsonb_typeof(p_registry) IS DISTINCT FROM 'object' OR jsonb_typeof(p_registry->'items') IS DISTINCT FROM 'array'
 OR jsonb_array_length(p_registry->'items')>72 OR octet_length(p_registry::text)>1048576
 OR p_registry->>'researchId' IS DISTINCT FROM p_run::text OR p_registry->>'actionable' IS DISTINCT FROM 'false'
 THEN RAISE EXCEPTION 'TOPIC_REGISTRY_INVALID' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_object_keys(p_registry) k WHERE k NOT IN ('researchId','validatedAt','version','revision','revisionStatus','buildCommit','items','coverage','auditTasks','pendingScopes','issues','actionable','registryAvailable','topicArchiveIssue','omission'))
 THEN RAISE EXCEPTION 'TOPIC_REGISTRY_FIELDS_INVALID' USING ERRCODE='23514'; END IF;
 SELECT r.run_mode,r.dispatched_at,q.tenant_id INTO v_mode,v_dispatch,v_scope FROM research.daily_research_runs r
 LEFT JOIN research.daily_research_refresh_requests q ON q.id=r.debug_request_id AND q.request_mode=r.run_mode WHERE r.run_id=p_run;
 IF NOT FOUND OR (v_mode<>'SCHEDULED' AND v_scope IS NULL) THEN RAISE EXCEPTION 'TOPIC_SCOPE_INVALID' USING ERRCODE='23514'; END IF;
 SELECT * INTO a FROM research.research_validation_artifacts WHERE run_id=p_run;
 IF NOT FOUND THEN RAISE EXCEPTION 'TOPIC_MATERIALS_MISSING' USING ERRCODE='23514'; END IF;
 SELECT * INTO i FROM research.research_validation_records WHERE run_id=p_run AND kind='INITIAL';
 IF NOT FOUND OR a.validated_at<>i.validated_at OR (p_registry->>'validatedAt')::timestamptz<>a.validated_at
 OR a.validated_at<v_dispatch OR a.validated_at>clock_timestamp() OR a.tenant_id IS DISTINCT FROM v_scope OR i.tenant_id IS DISTINCT FROM v_scope
 THEN RAISE EXCEPTION 'TOPIC_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
 SELECT frozen_bundle->'artifacts' INTO queued FROM research.research_validation_archive_queue WHERE run_id=p_run;
 IF FOUND AND queued IS DISTINCT FROM jsonb_build_object('packet',a.packet,'raw_output',a.raw_output,'source_checks',a.source_checks)
 THEN RAISE EXCEPTION 'TOPIC_MATERIAL_IDENTITY_INVALID' USING ERRCODE='23514'; END IF;
 FOREACH descriptor IN ARRAY ARRAY[a.packet,a.raw_output,a.source_checks] LOOP
   IF descriptor->>'content' IS NOT NULL AND (descriptor->>'sha256' IS DISTINCT FROM encode(sha256(convert_to(descriptor->>'content','UTF8')),'hex')
     OR (descriptor->>'byteLength')::bigint<>octet_length(convert_to(descriptor->>'content','UTF8')))
   THEN RAISE EXCEPTION 'TOPIC_MATERIAL_HASH_INVALID' USING ERRCODE='23514'; END IF;
 END LOOP;
 selected:=COALESCE(((a.packet->>'content')::jsonb)#>'{researchDiscovery,auditManifest,selectedScopes}','[]'::jsonb);
 IF jsonb_typeof(selected)<>'array' OR jsonb_array_length(selected)>10 THEN RAISE EXCEPTION 'TOPIC_MANIFEST_INVALID' USING ERRCODE='23514'; END IF;
 IF octet_length(((p_registry-'items')||jsonb_build_object('selectedScopes',selected))::text)
 +COALESCE((SELECT sum(octet_length(value::text)) FROM jsonb_array_elements(p_registry->'items')),0)>1048576
 THEN RAISE EXCEPTION 'TOPIC_REGISTRY_SIZE_LIMIT' USING ERRCODE='23514'; END IF;
 v_digest:=encode(sha256(convert_to(p_registry::text,'UTF8')),'hex');
 SELECT id,content_digest INTO v_id,existing FROM research.research_topic_snapshots WHERE run_id=p_run;
 IF FOUND THEN
   IF existing<>v_digest THEN RAISE EXCEPTION 'TOPIC_IMMUTABLE_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN v_id;
 END IF;
 INSERT INTO research.research_topic_snapshots(run_id,tenant_id,initial_record_id,validated_at,total_count,content_digest,material_hashes,metadata)
 VALUES(p_run,v_scope,i.id,a.validated_at,jsonb_array_length(p_registry->'items'),v_digest,
 jsonb_build_object('PACKET',a.packet->>'sha256','RAW_MODEL',a.raw_output->>'sha256','SOURCE_CHECKS',a.source_checks->>'sha256'),(p_registry-'items')||jsonb_build_object('selectedScopes',selected)) RETURNING id INTO v_id;
 FOR row IN SELECT value FROM jsonb_array_elements(p_registry->'items') LOOP
   IF jsonb_typeof(row) IS DISTINCT FROM 'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(row) k WHERE k NOT IN ('topicId','key','title','summary','status','reasonCode','reason','impact','measures','pendingTasks','sourceIds','match','investmentEligible'))
   THEN RAISE EXCEPTION 'TOPIC_ITEM_INVALID' USING ERRCODE='23514'; END IF;
   n:=n+1;
   INSERT INTO research.research_topic_entries(snapshot_id,ordinal,topic_id,status,investment_eligible,body)
   VALUES(v_id,n,row->>'topicId',row->>'status',(row->>'investmentEligible')::boolean,row);
 END LOOP;
 RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION research.save_topic_registry(UUID,JSONB) FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
GRANT EXECUTE ON FUNCTION research.save_topic_registry(UUID,JSONB) TO wealthsail_worker;
COMMENT ON TABLE research.research_topic_snapshots IS 'Immutable bounded per-run observations; no current trading, risk, study-origin or notification consumer.';
COMMENT ON TABLE research.research_topic_entries IS 'Safe paginated topic bodies only. No raw model material, plans, prices or mutable pending-work execution.';
