-- Add bounded read-only insight metadata. Keep the existing immutable snapshot,
-- frozen identity, RLS and worker-only write contracts from migration 102.
-- Transaction ownership stays with the migration runner and checksum ledger.

CREATE OR REPLACE FUNCTION research.save_topic_registry(p_run UUID,p_registry JSONB) RETURNS UUID
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a research.research_validation_artifacts%ROWTYPE; i research.research_validation_records%ROWTYPE;
 v_scope UUID; v_mode TEXT; v_dispatch TIMESTAMPTZ; v_id UUID; v_digest TEXT; row JSONB; n INTEGER:=0; existing TEXT; selected JSONB; descriptor JSONB; queued JSONB; field TEXT;
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
   IF jsonb_typeof(row) IS DISTINCT FROM 'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(row) k WHERE k NOT IN ('topicId','key','title','summary','status','reasonCode','reason','impact','measures','pendingTasks','sourceIds','match','investmentEligible','unknowns','followUp','insightCategory','evidenceCoverageScore'))
   THEN RAISE EXCEPTION 'TOPIC_ITEM_INVALID' USING ERRCODE='23514'; END IF;
   FOREACH field IN ARRAY ARRAY['unknowns','followUp'] LOOP
     IF row ? field THEN
       IF jsonb_typeof(row->field) IS DISTINCT FROM 'array' OR jsonb_array_length(row->field)>8
       THEN RAISE EXCEPTION 'TOPIC_INSIGHT_METADATA_INVALID' USING ERRCODE='23514'; END IF;
       IF EXISTS(SELECT 1 FROM jsonb_array_elements(row->field) e WHERE jsonb_typeof(e) IS DISTINCT FROM 'string' OR char_length(e#>>'{}') NOT BETWEEN 1 AND 300)
       THEN RAISE EXCEPTION 'TOPIC_INSIGHT_METADATA_INVALID' USING ERRCODE='23514'; END IF;
     END IF;
   END LOOP;
   IF row ? 'insightCategory' AND (jsonb_typeof(row->'insightCategory') IS DISTINCT FROM 'string' OR row->>'insightCategory' NOT IN ('DYNAMIC','INDUSTRY','MULTI_SCOPE'))
   THEN RAISE EXCEPTION 'TOPIC_INSIGHT_METADATA_INVALID' USING ERRCODE='23514'; END IF;
   IF row ? 'evidenceCoverageScore' AND (jsonb_typeof(row->'evidenceCoverageScore') IS DISTINCT FROM 'number' OR row->>'evidenceCoverageScore' !~ '^(0|[1-9][0-9]?|100)$')
   THEN RAISE EXCEPTION 'TOPIC_INSIGHT_METADATA_INVALID' USING ERRCODE='23514'; END IF;
   n:=n+1;
   INSERT INTO research.research_topic_entries(snapshot_id,ordinal,topic_id,status,investment_eligible,body)
   VALUES(v_id,n,row->>'topicId',row->>'status',(row->>'investmentEligible')::boolean,row);
 END LOOP;
 RETURN v_id;
END $$;

-- CREATE OR REPLACE preserves the original grants; restate the narrow boundary.
REVOKE ALL ON FUNCTION research.save_topic_registry(UUID,JSONB) FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
GRANT EXECUTE ON FUNCTION research.save_topic_registry(UUID,JSONB) TO wealthsail_worker;

