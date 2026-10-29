BEGIN;

-- Harden RLS policies against empty string request.jwt.claims
DROP POLICY IF EXISTS ai_usage_task_read ON research.ai_usage_tasks;
CREATE POLICY ai_usage_task_read ON research.ai_usage_tasks FOR SELECT TO wealthsail_api USING (
    (tenant_id::text = (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb->>'tenant_id')
      AND (user_id = auth.uid() OR identity.has_tenant_role(tenant_id, ARRAY['OWNER','ADMIN'])))
    OR (tenant_id IS NULL AND EXISTS (
        SELECT 1 FROM identity.platform_roles p WHERE p.user_id = auth.uid()
          AND p.role = 'PLATFORM_ADMIN' AND p.status = 'ACTIVE'))
);

DROP POLICY IF EXISTS ai_usage_task_insert ON research.ai_usage_tasks;
CREATE POLICY ai_usage_task_insert ON research.ai_usage_tasks FOR INSERT TO wealthsail_api WITH CHECK (
    tenant_id::text = (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb->>'tenant_id')
    AND user_id = auth.uid() AND identity.is_tenant_member(tenant_id)
);

DROP POLICY IF EXISTS ai_usage_task_update ON research.ai_usage_tasks;
CREATE POLICY ai_usage_task_update ON research.ai_usage_tasks FOR UPDATE TO wealthsail_api
    USING (user_id = auth.uid() AND tenant_id::text = (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb->>'tenant_id'))
    WITH CHECK (user_id = auth.uid() AND tenant_id::text = (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb->>'tenant_id'));

DROP POLICY IF EXISTS ai_usage_call_insert ON research.ai_usage_calls;
CREATE POLICY ai_usage_call_insert ON research.ai_usage_calls FOR INSERT TO wealthsail_api WITH CHECK (
    EXISTS (SELECT 1 FROM research.ai_usage_tasks t WHERE t.id = task_id AND t.user_id = auth.uid()
      AND t.tenant_id::text = (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb->>'tenant_id'))
);

DROP POLICY IF EXISTS ai_usage_call_update ON research.ai_usage_calls;
CREATE POLICY ai_usage_call_update ON research.ai_usage_calls FOR UPDATE TO wealthsail_api
    USING (EXISTS (SELECT 1 FROM research.ai_usage_tasks t WHERE t.id = task_id AND t.user_id = auth.uid()
      AND t.tenant_id::text = (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb->>'tenant_id')));

-- Replace mark_ai_usage_overlap with clear separation between worker execution
-- and tenant authentication, avoiding invalid JSON cast on missing/empty claims.
CREATE OR REPLACE FUNCTION research.mark_ai_usage_overlap(p_call UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research AS $$
DECLARE
    v_call research.ai_usage_calls%ROWTYPE;
    v_claims_raw TEXT;
    v_claims JSONB;
    v_tenant_id TEXT;
    v_is_worker BOOLEAN;
BEGIN
    v_is_worker := (
        current_setting('role', true) = 'wealthsail_worker'
        OR SESSION_USER = 'wealthsail_worker'
        OR pg_has_role(SESSION_USER, 'wealthsail_worker', 'MEMBER')
    );

    IF NOT v_is_worker THEN
        v_claims_raw := NULLIF(current_setting('request.jwt.claims', true), '');
        IF v_claims_raw IS NULL THEN
            RAISE EXCEPTION 'AI usage observation is not owned by caller' USING ERRCODE='42501';
        END IF;

        BEGIN
            v_claims := v_claims_raw::jsonb;
        EXCEPTION WHEN others THEN
            RAISE EXCEPTION 'AI usage observation is not owned by caller' USING ERRCODE='42501';
        END;

        v_tenant_id := v_claims->>'tenant_id';
        IF v_tenant_id IS NULL OR auth.uid() IS NULL THEN
            RAISE EXCEPTION 'AI usage observation is not owned by caller' USING ERRCODE='42501';
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM research.ai_usage_calls c
            JOIN research.ai_usage_tasks t ON t.id = c.task_id
            WHERE c.id = p_call
              AND t.user_id = auth.uid()
              AND t.tenant_id::text = v_tenant_id
        ) THEN
            RAISE EXCEPTION 'AI usage observation is not owned by caller' USING ERRCODE='42501';
        END IF;
    END IF;

    SELECT * INTO v_call FROM research.ai_usage_calls WHERE id = p_call;
    IF v_call.account_fingerprint IS NULL OR v_call.finished_at IS NULL THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM research.ai_usage_calls c
        WHERE c.id <> p_call
          AND c.account_fingerprint = v_call.account_fingerprint
          AND c.started_at < v_call.finished_at
          AND COALESCE(c.finished_at, statement_timestamp()) > v_call.started_at
    ) THEN
        UPDATE research.ai_usage_calls c SET concurrent = TRUE
        WHERE c.account_fingerprint = v_call.account_fingerprint
          AND c.started_at < v_call.finished_at
          AND COALESCE(c.finished_at, statement_timestamp()) > v_call.started_at;
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION research.mark_ai_usage_overlap(UUID) FROM PUBLIC,authenticated;
GRANT EXECUTE ON FUNCTION research.mark_ai_usage_overlap(UUID) TO wealthsail_api,wealthsail_worker;

COMMIT;
