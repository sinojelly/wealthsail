BEGIN;

-- API requests may enqueue one fixed fund-chart task; Worker lease columns
-- remain inaccessible to ordinary API UPDATE statements.
CREATE OR REPLACE FUNCTION workflow.enqueue_fund_nav_chart_fetch(
    p_tenant UUID, p_user UUID, p_code TEXT
)
RETURNS TABLE(id UUID, status TEXT, reason TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = workflow, pg_catalog
AS $$
DECLARE
    v_key TEXT;
    v_now TIMESTAMPTZ := statement_timestamp();
    v_job workflow.job_runs%ROWTYPE;
BEGIN
    IF auth.uid() IS DISTINCT FROM p_user
       OR (current_setting('request.jwt.claims', true)::jsonb->>'tenant_id')
           IS DISTINCT FROM p_tenant::text
       OR NOT EXISTS (
           SELECT 1 FROM identity.tenant_members m
           JOIN identity.tenants t ON t.id=m.tenant_id AND t.status='ACTIVE'
           WHERE m.tenant_id=p_tenant AND m.user_id=p_user
             AND m.status='ACTIVE' AND m.role IN ('OWNER','ADMIN')
       ) THEN
        RAISE EXCEPTION 'NAV chart request requires an active tenant operator'
            USING ERRCODE='42501';
    END IF;
    IF p_code IS NULL OR p_code !~ '^[0-9]{6}$' OR (
        SELECT count(*) FROM reference.research_fund_catalog c
        WHERE c.fund_code=p_code AND c.market='FUND_CN'
          AND c.trade_form IN ('FUND','LOF') AND c.currency='CNY'
          AND c.fund_name !~ '美元|港币|港元|外币|现汇|现钞'
    ) <> 1 THEN
        RAISE EXCEPTION 'NAV chart requires a verified unique CNY fund';
    END IF;
    v_key := 'fund-unit-nav-history:' || p_code;
    PERFORM pg_advisory_xact_lock(hashtext(p_tenant::text || ':' || v_key));
    SELECT j.* INTO v_job FROM workflow.job_runs j
      WHERE j.tenant_id=p_tenant AND j.job_key=v_key FOR UPDATE;
    IF FOUND THEN
        IF v_job.job_type IS DISTINCT FROM 'FUND_UNIT_NAV_HISTORY_FETCH' THEN
            RAISE EXCEPTION 'NAV chart job identity mismatch';
        END IF;
        IF v_job.status IN ('PENDING','RUNNING') THEN
            RETURN QUERY SELECT v_job.id,v_job.status,NULL::text;
            RETURN;
        END IF;
        IF v_job.finished_at IS NOT NULL AND v_job.finished_at>v_now-interval '1 hour' THEN
            RETURN QUERY SELECT v_job.id,v_job.status,'SOURCE_COOLDOWN'::text;
            RETURN;
        END IF;
        UPDATE workflow.job_runs j SET status='PENDING',available_at=v_now,
          started_at=v_now,finished_at=NULL,error_class=NULL,next_retry_at=NULL,
          attempt_count=0,lease_token=NULL,lease_owner=NULL,lease_until=NULL,
          heartbeat_at=NULL,
          payload=jsonb_build_object('fundCode',p_code,'requestedBy',p_user::text)
          WHERE j.id=v_job.id AND j.tenant_id=p_tenant
          RETURNING j.* INTO v_job;
    ELSE
        INSERT INTO workflow.job_runs(tenant_id,job_key,status,job_type,payload,available_at)
          VALUES(p_tenant,v_key,'PENDING','FUND_UNIT_NAV_HISTORY_FETCH',
            jsonb_build_object('fundCode',p_code,'requestedBy',p_user::text),v_now)
          RETURNING * INTO v_job;
    END IF;
    RETURN QUERY SELECT v_job.id,v_job.status,NULL::text;
END;
$$;

REVOKE ALL ON FUNCTION workflow.enqueue_fund_nav_chart_fetch(UUID,UUID,TEXT)
    FROM PUBLIC,authenticated,wealthsail_worker;
GRANT EXECUTE ON FUNCTION workflow.enqueue_fund_nav_chart_fetch(UUID,UUID,TEXT)
    TO wealthsail_api;

COMMIT;
