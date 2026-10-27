BEGIN;

CREATE TABLE research.ai_usage_tasks (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES identity.tenants(id),
    user_id UUID REFERENCES identity.profiles(id),
    task_type TEXT NOT NULL CHECK (length(task_type) BETWEEN 1 AND 80),
    task_name TEXT NOT NULL CHECK (length(task_name) BETWEEN 1 AND 160),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    business_status TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK (business_status IN ('RUNNING','SUCCEEDED','FAILED','DEGRADED','CANCELLED')),
    error_class TEXT,
    CHECK ((tenant_id IS NULL) = (user_id IS NULL)),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX ai_usage_tasks_tenant_started ON research.ai_usage_tasks(tenant_id,started_at DESC,id);

CREATE TABLE research.ai_usage_calls (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES research.ai_usage_tasks(id),
    model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 120),
    call_type TEXT NOT NULL CHECK (call_type IN ('completion','research','chat')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','UNCERTAIN')),
    error_class TEXT,
    details JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details)='object'),
    account_fingerprint TEXT,
    concurrent BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX ai_usage_calls_task ON research.ai_usage_calls(task_id,started_at,id);
CREATE INDEX ai_usage_calls_account ON research.ai_usage_calls(account_fingerprint,started_at);

ALTER TABLE research.ai_usage_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.ai_usage_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE research.ai_usage_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.ai_usage_calls FORCE ROW LEVEL SECURITY;

CREATE POLICY ai_usage_task_read ON research.ai_usage_tasks FOR SELECT TO wealthsail_api USING (
    (tenant_id::text=current_setting('request.jwt.claims',true)::jsonb->>'tenant_id'
      AND (user_id=auth.uid() OR identity.has_tenant_role(tenant_id,ARRAY['OWNER','ADMIN'])))
    OR (tenant_id IS NULL AND EXISTS (
        SELECT 1 FROM identity.platform_roles p WHERE p.user_id=auth.uid()
          AND p.role='PLATFORM_ADMIN' AND p.status='ACTIVE'))
);
CREATE POLICY ai_usage_task_insert ON research.ai_usage_tasks FOR INSERT TO wealthsail_api WITH CHECK (
    tenant_id::text=current_setting('request.jwt.claims',true)::jsonb->>'tenant_id'
    AND user_id=auth.uid() AND identity.is_tenant_member(tenant_id)
);
CREATE POLICY ai_usage_task_update ON research.ai_usage_tasks FOR UPDATE TO wealthsail_api
    USING (user_id=auth.uid() AND tenant_id::text=current_setting('request.jwt.claims',true)::jsonb->>'tenant_id')
    WITH CHECK (user_id=auth.uid() AND tenant_id::text=current_setting('request.jwt.claims',true)::jsonb->>'tenant_id');

CREATE POLICY ai_usage_call_read ON research.ai_usage_calls FOR SELECT TO wealthsail_api USING (
    EXISTS (SELECT 1 FROM research.ai_usage_tasks t WHERE t.id=task_id)
);
CREATE POLICY ai_usage_call_insert ON research.ai_usage_calls FOR INSERT TO wealthsail_api WITH CHECK (
    EXISTS (SELECT 1 FROM research.ai_usage_tasks t WHERE t.id=task_id AND t.user_id=auth.uid()
      AND t.tenant_id::text=current_setting('request.jwt.claims',true)::jsonb->>'tenant_id')
);
CREATE POLICY ai_usage_call_update ON research.ai_usage_calls FOR UPDATE TO wealthsail_api
    USING (EXISTS (SELECT 1 FROM research.ai_usage_tasks t WHERE t.id=task_id AND t.user_id=auth.uid()
      AND t.tenant_id::text=current_setting('request.jwt.claims',true)::jsonb->>'tenant_id'));

REVOKE ALL ON research.ai_usage_tasks,research.ai_usage_calls FROM PUBLIC,authenticated;
GRANT SELECT,INSERT ON research.ai_usage_tasks,research.ai_usage_calls TO wealthsail_api,wealthsail_worker;
GRANT UPDATE(business_status,finished_at,error_class) ON research.ai_usage_tasks TO wealthsail_api,wealthsail_worker;
GRANT UPDATE(finished_at,status,model,error_class,details,account_fingerprint) ON research.ai_usage_calls TO wealthsail_api,wealthsail_worker;

-- Mark both observations when their sampling intervals overlap on the shared
-- account. The definer can only change this flag, never reveal another task.
CREATE FUNCTION research.mark_ai_usage_overlap(p_call UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research AS $$
DECLARE v_call research.ai_usage_calls%ROWTYPE;
BEGIN
    IF current_setting('role',true) IS DISTINCT FROM 'wealthsail_worker'
       AND NOT EXISTS (
        SELECT 1 FROM research.ai_usage_calls c JOIN research.ai_usage_tasks t ON t.id=c.task_id
         WHERE c.id=p_call AND t.user_id=auth.uid()
           AND t.tenant_id::text=current_setting('request.jwt.claims',true)::jsonb->>'tenant_id'
    ) THEN
        RAISE EXCEPTION 'AI usage observation is not owned by caller' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_call FROM research.ai_usage_calls WHERE id=p_call;
    IF v_call.account_fingerprint IS NULL OR v_call.finished_at IS NULL THEN RETURN; END IF;
    IF EXISTS (SELECT 1 FROM research.ai_usage_calls c WHERE c.id<>p_call
        AND c.account_fingerprint=v_call.account_fingerprint
        AND c.started_at<v_call.finished_at
        AND COALESCE(c.finished_at,statement_timestamp())>v_call.started_at) THEN
        UPDATE research.ai_usage_calls c SET concurrent=TRUE
        WHERE c.account_fingerprint=v_call.account_fingerprint
          AND c.started_at<v_call.finished_at
          AND COALESCE(c.finished_at,statement_timestamp())>v_call.started_at;
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION research.mark_ai_usage_overlap(UUID) FROM PUBLIC,authenticated;
GRANT EXECUTE ON FUNCTION research.mark_ai_usage_overlap(UUID) TO wealthsail_api,wealthsail_worker;

COMMIT;
