BEGIN;

-- Safe helper to extract claims without failing on empty, missing, or malformed JSON
CREATE OR REPLACE FUNCTION research.safe_jwt_claim(claim_key TEXT)
RETURNS TEXT
LANGUAGE plpgsql STABLE SET search_path = pg_catalog AS $$
DECLARE
    v_raw TEXT;
    v_json JSONB;
BEGIN
    v_raw := NULLIF(current_setting('request.jwt.claims', true), '');
    IF v_raw IS NULL THEN
        RETURN NULL;
    END IF;
    BEGIN
        v_json := v_raw::jsonb;
    EXCEPTION WHEN others THEN
        RETURN NULL;
    END;
    RETURN v_json->>claim_key;
END;
$$;

REVOKE ALL ON FUNCTION research.safe_jwt_claim(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.safe_jwt_claim(TEXT) TO wealthsail_api,wealthsail_worker;

-- Safe helper to extract caller user UUID without failing on missing, empty, or malformed UUID
CREATE OR REPLACE FUNCTION research.safe_jwt_uid()
RETURNS UUID
LANGUAGE plpgsql STABLE SET search_path = pg_catalog AS $$
DECLARE
    v_sub TEXT;
BEGIN
    v_sub := research.safe_jwt_claim('sub');
    IF v_sub IS NULL OR v_sub = '' THEN
        RETURN NULL;
    END IF;
    BEGIN
        RETURN v_sub::uuid;
    EXCEPTION WHEN others THEN
        RETURN NULL;
    END;
END;
$$;

REVOKE ALL ON FUNCTION research.safe_jwt_uid() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.safe_jwt_uid() TO wealthsail_api,wealthsail_worker;

-- Partial index for bounded scans when polling or reconciling orphaned running calls
CREATE INDEX IF NOT EXISTS ai_usage_calls_running
    ON research.ai_usage_calls (started_at, id)
    WHERE status = 'RUNNING';

-- Harden RLS policies against empty or malformed request.jwt.claims and invalid user sub
DROP POLICY IF EXISTS ai_usage_task_read ON research.ai_usage_tasks;
CREATE POLICY ai_usage_task_read ON research.ai_usage_tasks FOR SELECT TO wealthsail_api USING (
    (tenant_id::text = research.safe_jwt_claim('tenant_id')
      AND (
          user_id = research.safe_jwt_uid()
          OR (
              research.safe_jwt_uid() IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM identity.tenant_members tm
                  WHERE tm.tenant_id = research.ai_usage_tasks.tenant_id
                    AND tm.user_id = research.safe_jwt_uid()
                    AND tm.status = 'ACTIVE'
                    AND tm.role = ANY (ARRAY['OWNER','ADMIN'])
              )
          )
      ))
    OR (tenant_id IS NULL AND EXISTS (
        SELECT 1 FROM identity.platform_roles p WHERE p.user_id = research.safe_jwt_uid()
          AND p.role = 'PLATFORM_ADMIN' AND p.status = 'ACTIVE'))
);

DROP POLICY IF EXISTS ai_usage_task_insert ON research.ai_usage_tasks;
CREATE POLICY ai_usage_task_insert ON research.ai_usage_tasks FOR INSERT TO wealthsail_api WITH CHECK (
    tenant_id::text = research.safe_jwt_claim('tenant_id')
    AND user_id = research.safe_jwt_uid()
    AND research.safe_jwt_uid() IS NOT NULL
    AND EXISTS (
        SELECT 1 FROM identity.tenant_members tm
        WHERE tm.tenant_id = research.ai_usage_tasks.tenant_id
          AND tm.user_id = research.safe_jwt_uid()
          AND tm.status = 'ACTIVE'
    )
);

DROP POLICY IF EXISTS ai_usage_task_update ON research.ai_usage_tasks;
CREATE POLICY ai_usage_task_update ON research.ai_usage_tasks FOR UPDATE TO wealthsail_api
    USING (user_id = research.safe_jwt_uid() AND tenant_id::text = research.safe_jwt_claim('tenant_id'))
    WITH CHECK (user_id = research.safe_jwt_uid() AND tenant_id::text = research.safe_jwt_claim('tenant_id'));

DROP POLICY IF EXISTS ai_usage_call_insert ON research.ai_usage_calls;
CREATE POLICY ai_usage_call_insert ON research.ai_usage_calls FOR INSERT TO wealthsail_api WITH CHECK (
    EXISTS (SELECT 1 FROM research.ai_usage_tasks t WHERE t.id = task_id AND t.user_id = research.safe_jwt_uid()
      AND t.tenant_id::text = research.safe_jwt_claim('tenant_id'))
);

DROP POLICY IF EXISTS ai_usage_call_update ON research.ai_usage_calls;
CREATE POLICY ai_usage_call_update ON research.ai_usage_calls FOR UPDATE TO wealthsail_api
    USING (EXISTS (SELECT 1 FROM research.ai_usage_tasks t WHERE t.id = task_id AND t.user_id = research.safe_jwt_uid()
      AND t.tenant_id::text = research.safe_jwt_claim('tenant_id')));

-- Replace mark_ai_usage_overlap with strict worker authentication and safe claims extraction
CREATE OR REPLACE FUNCTION research.mark_ai_usage_overlap(p_call UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research AS $$
DECLARE
    v_call research.ai_usage_calls%ROWTYPE;
    v_tenant_id TEXT;
    v_user_id UUID;
    v_is_worker BOOLEAN;
BEGIN
    v_is_worker := (
        current_setting('role', true) = 'wealthsail_worker'
        AND (
            SESSION_USER = 'wealthsail_worker'
            OR pg_has_role(SESSION_USER, 'wealthsail_worker', 'USAGE')
            OR pg_has_role(SESSION_USER, 'wealthsail_worker', 'MEMBER')
        )
    );

    IF NOT v_is_worker THEN
        v_tenant_id := research.safe_jwt_claim('tenant_id');
        v_user_id := research.safe_jwt_uid();
        IF v_tenant_id IS NULL OR v_user_id IS NULL THEN
            RAISE EXCEPTION 'AI usage observation is not owned by caller' USING ERRCODE='42501';
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM research.ai_usage_calls c
            JOIN research.ai_usage_tasks t ON t.id = c.task_id
            WHERE c.id = p_call
              AND t.user_id = v_user_id
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

