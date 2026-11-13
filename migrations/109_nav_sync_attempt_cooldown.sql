CREATE TABLE market.fund_nav_sync_attempts (
    fund_code TEXT PRIMARY KEY CHECK (fund_code ~ '^[0-9]{6}$'),
    last_sync_attempt_at TIMESTAMPTZ NOT NULL
);
REVOKE ALL ON market.fund_nav_sync_attempts FROM PUBLIC, authenticated, wealthsail_api, wealthsail_worker;
GRANT SELECT ON market.fund_nav_sync_attempts TO wealthsail_api, wealthsail_worker;

CREATE FUNCTION market.claim_fund_nav_sync_attempt(p_code TEXT)
RETURNS TABLE(claimed BOOLEAN, last_sync_attempt_at TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, market AS $$
DECLARE
    v_now TIMESTAMPTZ := statement_timestamp();
    v_attempt TIMESTAMPTZ;
BEGIN
    IF p_code IS NULL OR p_code !~ '^[0-9]{6}$' OR (
        SELECT count(*) FROM reference.research_fund_catalog c
        WHERE c.fund_code=p_code AND c.market='FUND_CN'
          AND c.trade_form IN ('FUND','LOF') AND c.currency='CNY'
          AND c.fund_name !~ '美元|港币|港元|外币|现汇|现钞'
    ) <> 1 THEN
        RAISE EXCEPTION 'NAV sync requires a unique CNY fund';
    END IF;
    INSERT INTO market.fund_nav_sync_attempts AS attempts(fund_code,last_sync_attempt_at)
      VALUES(p_code,v_now)
      ON CONFLICT(fund_code) DO UPDATE SET last_sync_attempt_at=EXCLUDED.last_sync_attempt_at
        WHERE attempts.last_sync_attempt_at<=v_now-interval '12 hours'
      RETURNING attempts.last_sync_attempt_at INTO v_attempt;
    IF FOUND THEN
        RETURN QUERY SELECT TRUE,v_attempt;
    ELSE
        RETURN QUERY SELECT FALSE,a.last_sync_attempt_at
          FROM market.fund_nav_sync_attempts a WHERE a.fund_code=p_code;
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION market.claim_fund_nav_sync_attempt(TEXT) FROM PUBLIC,authenticated,wealthsail_api;
GRANT EXECUTE ON FUNCTION market.claim_fund_nav_sync_attempt(TEXT) TO wealthsail_worker;

CREATE FUNCTION market.claim_member_fund_nav_sync_attempt(p_tenant UUID,p_user UUID,p_code TEXT)
RETURNS TABLE(claimed BOOLEAN, last_sync_attempt_at TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, market AS $$
BEGIN
    IF auth.uid() IS DISTINCT FROM p_user
       OR (current_setting('request.jwt.claims',true)::jsonb->>'tenant_id') IS DISTINCT FROM p_tenant::text
       OR NOT EXISTS (
         SELECT 1 FROM identity.tenant_members m JOIN identity.tenants t ON t.id=m.tenant_id
         WHERE m.tenant_id=p_tenant AND m.user_id=p_user AND m.status='ACTIVE' AND t.status='ACTIVE'
       ) THEN
        RAISE EXCEPTION 'NAV sync requires an active tenant member' USING ERRCODE='42501';
    END IF;
    RETURN QUERY SELECT * FROM market.claim_fund_nav_sync_attempt(p_code);
END;
$$;
REVOKE ALL ON FUNCTION market.claim_member_fund_nav_sync_attempt(UUID,UUID,TEXT) FROM PUBLIC,authenticated,wealthsail_worker;
GRANT EXECUTE ON FUNCTION market.claim_member_fund_nav_sync_attempt(UUID,UUID,TEXT) TO wealthsail_api;
