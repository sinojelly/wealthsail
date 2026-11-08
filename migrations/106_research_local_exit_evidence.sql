-- Diagnostic summaries are never exit authority. The trusted worker retains
-- private JobObject handles; reboot recovery is an explicit local OWNER action.
ALTER TABLE research.daily_research_model_guards
 ADD COLUMN exit_evidence JSONB CHECK(exit_evidence IS NULL OR jsonb_typeof(exit_evidence)='object'),
 ADD COLUMN release_proof JSONB CHECK(release_proof IS NULL OR (
   jsonb_typeof(release_proof)='object' AND release_proof ? 'kind'
   AND release_proof->>'kind' IN ('LOCAL_WINDOWS_JOB','LOCAL_WINDOWS_REBOOT','REMOTE_RUNNER')));
GRANT UPDATE(exit_evidence,release_proof) ON research.daily_research_model_guards TO wealthsail_worker;

CREATE TABLE research.local_research_recovery_audit (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 run_id UUID NOT NULL REFERENCES research.daily_research_model_guards(run_id),
 guard_snapshot TEXT NOT NULL CHECK(guard_snapshot ~ '^[0-9a-f]{64}$'),
 acquired_at TIMESTAMPTZ NOT NULL,
 tenant_id UUID NOT NULL,
 user_id UUID NOT NULL,
 released_at TIMESTAMPTZ NOT NULL CHECK(released_at>acquired_at),
 proof JSONB NOT NULL CHECK(jsonb_typeof(proof)='object' AND proof ? 'kind'
   AND proof->>'kind'='LOCAL_WINDOWS_REBOOT' AND NOT(proof ? 'exitConfirmed')),
 expired_queued INTEGER NOT NULL CHECK(expired_queued>=0),
 recovered_request BOOLEAN NOT NULL,
 queue_cutoff_at TIMESTAMPTZ NOT NULL CHECK(queue_cutoff_at>=acquired_at AND queue_cutoff_at<=released_at),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 UNIQUE(run_id,guard_snapshot)
);
ALTER TABLE research.local_research_recovery_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.local_research_recovery_audit FORCE ROW LEVEL SECURITY;
-- Migration-owned queue-control SECURITY DEFINER functions need only the
-- durable cutoff; API callers receive neither raw guard tokens nor audit rows.
CREATE POLICY local_research_recovery_owner_cutoff ON research.local_research_recovery_audit
 FOR SELECT TO CURRENT_USER USING(true);
REVOKE ALL ON research.local_research_recovery_audit FROM PUBLIC,authenticated,wealthsail_api;
GRANT SELECT,INSERT ON research.local_research_recovery_audit TO wealthsail_worker;
COMMENT ON TABLE research.local_research_recovery_audit IS
 'Manual local reboot evidence plus human origin/clock attestation, not a model lifecycle receipt. No TTL release, live process scan or immutable run/result rewrite.';

-- Column-scoped Worker UPDATE does not authorize this PostgreSQL table lock.
-- Expose only these fixed locks, without granting general table UPDATE. The
-- caller must already own the dispatch seal on this very database connection.
CREATE FUNCTION research.lock_local_research_maintenance()
RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE k BIGINT:=hashtext('research-model-dispatch')::bigint;
BEGIN
 IF current_setting('role',true)<>'wealthsail_worker'
   OR NOT pg_has_role(session_user,'wealthsail_worker','MEMBER') THEN
   RAISE EXCEPTION 'LOCAL_MAINTENANCE_WORKER_REQUIRED' USING ERRCODE='42501';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
   AND locktype='advisory' AND mode='ExclusiveLock' AND granted AND objsubid=1
   AND classid=((k>>32)&4294967295)::oid AND objid=(k&4294967295)::oid) THEN
   RAISE EXCEPTION 'LOCAL_MAINTENANCE_DISPATCH_SEAL_REQUIRED' USING ERRCODE='42501';
 END IF;
 LOCK TABLE research.daily_research_refresh_requests, research.daily_research_pipelines
   IN SHARE ROW EXCLUSIVE MODE NOWAIT;
END $$;
REVOKE ALL ON FUNCTION research.lock_local_research_maintenance()
 FROM PUBLIC,authenticated,wealthsail_api;
GRANT EXECUTE ON FUNCTION research.lock_local_research_maintenance() TO wealthsail_worker;
