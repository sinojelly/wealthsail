-- Optional discovery is computed by the archive worker, never by a page GET.
-- The existing fund_master name trigram index is provided by migration 068.
CREATE INDEX IF NOT EXISTS research_indices_name_trgm
 ON reference.research_indices USING gin(name gin_trgm_ops);

CREATE TABLE reference.opportunity_fund_catalog_revision (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
 revision BIGINT NOT NULL DEFAULT 1 CHECK(revision>0),
 transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id()
);
INSERT INTO reference.opportunity_fund_catalog_revision(singleton) VALUES(true);
REVOKE ALL ON reference.opportunity_fund_catalog_revision FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
GRANT SELECT(singleton,revision) ON reference.opportunity_fund_catalog_revision TO wealthsail_api,wealthsail_worker;

CREATE FUNCTION reference.advance_opportunity_fund_catalog_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE reference.opportunity_fund_catalog_revision SET revision=revision+1,transaction_id=pg_current_xact_id()
 WHERE singleton AND transaction_id<>pg_current_xact_id();
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION reference.advance_opportunity_fund_catalog_revision() FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
-- One increment per transaction, even when the full master upserts many rows.
CREATE TRIGGER opportunity_catalog_revision AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reference.fund_master
 FOR EACH STATEMENT EXECUTE FUNCTION reference.advance_opportunity_fund_catalog_revision();
CREATE TRIGGER opportunity_catalog_revision AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reference.research_indices
 FOR EACH STATEMENT EXECUTE FUNCTION reference.advance_opportunity_fund_catalog_revision();
CREATE TRIGGER opportunity_catalog_revision AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reference.product_index_links
 FOR EACH STATEMENT EXECUTE FUNCTION reference.advance_opportunity_fund_catalog_revision();
CREATE TRIGGER opportunity_catalog_revision AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reference.fund_index_links
 FOR EACH STATEMENT EXECUTE FUNCTION reference.advance_opportunity_fund_catalog_revision();
CREATE TRIGGER opportunity_catalog_revision AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reference.instruments
 FOR EACH STATEMENT EXECUTE FUNCTION reference.advance_opportunity_fund_catalog_revision();
CREATE TRIGGER opportunity_catalog_revision AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reference.instrument_aliases
 FOR EACH STATEMENT EXECUTE FUNCTION reference.advance_opportunity_fund_catalog_revision();

CREATE TABLE research.opportunity_fund_cache (
 run_id UUID PRIMARY KEY REFERENCES research.daily_research_runs(run_id),
 snapshot_id UUID NOT NULL REFERENCES research.research_topic_snapshots(id),
 content_digest TEXT NOT NULL CHECK(content_digest ~ '^[a-f0-9]{64}$'),
 validated_at TIMESTAMPTZ NOT NULL,
 match_version TEXT NOT NULL CHECK(match_version ~ '^[A-Za-z0-9_.-]{1,100}$'),
 catalog_revision BIGINT NOT NULL CHECK(catalog_revision>0),
 catalog_day DATE NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','COMPUTING','READY','FAILED')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 8),
 candidates JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(candidates)='object' AND octet_length(candidates::text)<=8388608),
 lease_token UUID,
 lease_expires_at TIMESTAMPTZ,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 last_error_class TEXT CHECK(last_error_class ~ '^[A-Za-z][A-Za-z0-9_]{0,79}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK((status='COMPUTING' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
   OR (status<>'COMPUTING' AND lease_token IS NULL AND lease_expires_at IS NULL))
);
CREATE INDEX opportunity_fund_cache_due ON research.opportunity_fund_cache(next_attempt_at,run_id)
 WHERE status IN ('PENDING','COMPUTING');

CREATE FUNCTION research.validate_opportunity_fund_cache() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE s research.research_topic_snapshots%ROWTYPE; item RECORD; fund JSONB; shares JSONB; count INTEGER;
BEGIN
 SELECT * INTO s FROM research.research_topic_snapshots WHERE id=NEW.snapshot_id;
 IF NOT FOUND OR s.run_id<>NEW.run_id OR s.validated_at<>NEW.validated_at OR s.content_digest<>NEW.content_digest
 THEN RAISE EXCEPTION 'OPPORTUNITY_CACHE_SNAPSHOT_MISMATCH' USING ERRCODE='23514'; END IF;
 IF NEW.status<>'READY' THEN
   IF NEW.candidates<>'{}'::jsonb THEN RAISE EXCEPTION 'OPPORTUNITY_CACHE_NOT_READY' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;
 IF (SELECT count(*) FROM jsonb_object_keys(NEW.candidates))<>(
   SELECT count(*) FROM research.research_topic_entries WHERE snapshot_id=s.id AND status<>'QUARANTINED')
 THEN RAISE EXCEPTION 'OPPORTUNITY_CACHE_TOPIC_MISMATCH' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT * FROM jsonb_each(NEW.candidates) LOOP
   IF NOT EXISTS(SELECT 1 FROM research.research_topic_entries WHERE snapshot_id=s.id AND topic_id=item.key AND status<>'QUARANTINED')
    OR jsonb_typeof(item.value) IS DISTINCT FROM 'object' OR item.value->>'topicId' IS DISTINCT FROM item.key
    OR EXISTS(SELECT 1 FROM jsonb_object_keys(item.value) k WHERE k NOT IN ('topicId','status','funds','indirectFunds','unavailableReason','message'))
    OR jsonb_typeof(item.value->'funds') IS DISTINCT FROM 'array' OR jsonb_typeof(item.value->'indirectFunds') IS DISTINCT FROM 'array'
   THEN RAISE EXCEPTION 'OPPORTUNITY_CACHE_TOPIC_INVALID' USING ERRCODE='23514'; END IF;
   count:=jsonb_array_length(item.value->'funds')+jsonb_array_length(item.value->'indirectFunds');
   IF count>96 THEN RAISE EXCEPTION 'OPPORTUNITY_CACHE_CANDIDATE_LIMIT' USING ERRCODE='23514'; END IF;
   FOR fund IN SELECT value FROM jsonb_array_elements((item.value->'funds')||(item.value->'indirectFunds')) LOOP
     IF jsonb_typeof(fund) IS DISTINCT FROM 'object' OR COALESCE(fund->>'fundCode','') !~ '^[0-9]{6}$'
      OR EXISTS(SELECT 1 FROM jsonb_object_keys(fund) k WHERE k NOT IN (
        'fundCode','name','subjectKind','subjectId','market','identityVerifiedAt','overseasCalendarRequired',
        'associationLevel','matchLevel','matchReason','sourceUri','fundGroupKey','alternativeShareClasses','salesChannel'))
     THEN RAISE EXCEPTION 'OPPORTUNITY_CACHE_FUND_INVALID' USING ERRCODE='23514'; END IF;
     IF fund ? 'alternativeShareClasses' THEN
       IF jsonb_typeof(fund->'alternativeShareClasses') IS DISTINCT FROM 'array' OR jsonb_array_length(fund->'alternativeShareClasses')>4
       THEN RAISE EXCEPTION 'OPPORTUNITY_CACHE_SHARES_INVALID' USING ERRCODE='23514'; END IF;
       FOR shares IN SELECT value FROM jsonb_array_elements(fund->'alternativeShareClasses') LOOP
         IF jsonb_typeof(shares)='object' AND EXISTS(SELECT 1 FROM jsonb_object_keys(shares) k WHERE k NOT IN ('fundCode','name','shareClass'))
         THEN RAISE EXCEPTION 'OPPORTUNITY_CACHE_SHARES_INVALID' USING ERRCODE='23514'; END IF;
       END LOOP;
     END IF;
   END LOOP;
 END LOOP;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION research.validate_opportunity_fund_cache() FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
CREATE TRIGGER validate_opportunity_fund_cache BEFORE INSERT OR UPDATE ON research.opportunity_fund_cache
 FOR EACH ROW EXECUTE FUNCTION research.validate_opportunity_fund_cache();

ALTER TABLE research.opportunity_fund_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.opportunity_fund_cache FORCE ROW LEVEL SECURITY;
REVOKE ALL ON research.opportunity_fund_cache FROM PUBLIC,authenticated,wealthsail_api,wealthsail_worker;
GRANT SELECT,INSERT,UPDATE ON research.opportunity_fund_cache TO wealthsail_worker;
-- Tokens never leave the worker. The API only receives stable bounded identities.
GRANT SELECT(run_id,snapshot_id,content_digest,validated_at,match_version,catalog_revision,catalog_day,
 status,attempts,candidates,next_attempt_at,last_error_class,created_at,updated_at)
 ON research.opportunity_fund_cache TO wealthsail_api;
CREATE POLICY opportunity_cache_owner ON research.opportunity_fund_cache TO CURRENT_USER USING(true) WITH CHECK(true);
CREATE POLICY opportunity_cache_worker ON research.opportunity_fund_cache TO wealthsail_worker USING(true) WITH CHECK(true);
CREATE POLICY opportunity_cache_member ON research.opportunity_fund_cache FOR SELECT TO wealthsail_api USING(
 EXISTS(SELECT 1 FROM research.research_topic_snapshots s WHERE s.id=snapshot_id AND s.run_id=opportunity_fund_cache.run_id));
COMMENT ON TABLE research.opportunity_fund_cache IS 'Optional archived topic discovery. Stable candidates only; live identity, purchase, NAV, scoring and execution facts are never cached. Rebuilt on snapshot, algorithm, catalog revision or catalog effective day changes.';
