BEGIN;
ALTER TABLE reference.research_indices ADD COLUMN collection_policy JSONB NOT NULL DEFAULT '{}'::jsonb
  CHECK(jsonb_typeof(collection_policy)='object');
COMMENT ON COLUMN reference.research_indices.collection_policy IS 'Versioned source/cutoff policy; retrieval-only research is not official publication or PIT evidence.';
UPDATE reference.research_indices SET provider_symbol=code,
 source_uri='https://www.csindex.com.cn/zh-CN/indices/index-detail/' || code,
 verified_at=now(),
 collection_policy='{"provider":"CSINDEX","timeBasis":"RETRIEVAL_ONLY","cutoffStrategy":"PREVIOUS_COMPLETE_SESSION","methodVersion":"csindex-retrieval-v1"}'::jsonb
 WHERE (id,code) IN (('hs300','000300'),('a500','000510'),('zz500','000905')) AND market='SSE'
   AND provider_symbol IS NULL AND source_uri IS NULL AND verified_at IS NULL
   AND publish_hour IS NULL AND collection_policy='{}'::jsonb;
COMMIT;
