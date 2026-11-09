BEGIN;

-- Only the trusted backend role can submit a normalized public NAV response.
-- Browser authenticated clients retain SELECT-only market access, and the API
-- never receives general INSERT rights or the ability to assume Worker roles.
CREATE OR REPLACE FUNCTION market.append_fund_nav_sync_history(
    p_tenant UUID, p_user UUID, p_code TEXT, p_payload JSONB
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, market
AS $$
DECLARE
    v_now TIMESTAMPTZ := statement_timestamp();
    v_retrieved TIMESTAMPTZ;
    v_points JSONB;
    v_point JSONB;
    v_day DATE;
    v_previous DATE;
    v_value NUMERIC;
    v_id UUID;
BEGIN
    IF auth.uid() IS DISTINCT FROM p_user
       OR (current_setting('request.jwt.claims', true)::jsonb->>'tenant_id')
          IS DISTINCT FROM p_tenant::text
       OR NOT EXISTS (
           SELECT 1 FROM identity.tenant_members m
           JOIN identity.tenants t ON t.id=m.tenant_id AND t.status='ACTIVE'
           WHERE m.tenant_id=p_tenant AND m.user_id=p_user AND m.status='ACTIVE'
       ) THEN
        RAISE EXCEPTION 'NAV cache append requires an active tenant member'
            USING ERRCODE='42501';
    END IF;
    IF p_code IS NULL OR p_code !~ '^[0-9]{6}$' OR (
        SELECT count(*) FROM reference.research_fund_catalog c
        WHERE c.fund_code=p_code AND c.market='FUND_CN'
          AND c.trade_form IN ('FUND','LOF') AND c.currency='CNY'
          AND c.fund_name !~ '美元|港币|港元|外币|现汇|现钞'
    ) <> 1 THEN
        RAISE EXCEPTION 'NAV cache append requires a verified unique CNY fund';
    END IF;
    IF jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
       OR octet_length(p_payload::text)>3000000
       OR p_payload->>'fundCode' IS DISTINCT FROM p_code
       OR p_payload->>'priceBasis' IS DISTINCT FROM 'UNIT_NAV'
       OR coalesce(p_payload->>'currency','CNY') <> 'CNY'
       OR coalesce(p_payload->>'unit','CNY') NOT IN ('CNY','CNY/份','元/份','CNY/unit')
       OR p_payload->>'sourceUri' IS NULL
       OR p_payload->>'sourceUri' NOT IN (
           'https://fund.eastmoney.com/' || p_code || '.html',
           'https://fund.eastmoney.com/pingzhongdata/' || p_code || '.js',
           'https://api.fund.eastmoney.com/f10/lsjz'
       ) OR coalesce((p_payload->>'fullHistory')::boolean,FALSE) THEN
        RAISE EXCEPTION 'invalid public unit NAV identity or source';
    END IF;
    v_retrieved := (p_payload->>'retrievedAt')::timestamptz;
    IF v_retrieved IS NULL OR v_retrieved>v_now+interval '5 seconds'
       OR v_retrieved<v_now-interval '36 hours'
       OR (p_payload->>'availableAt')::timestamptz IS DISTINCT FROM v_retrieved THEN
        RAISE EXCEPTION 'invalid NAV acquisition clock';
    END IF;
    v_points := p_payload->'points';
    IF jsonb_typeof(v_points) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'NAV coordinates must be an array';
    END IF;
    IF jsonb_array_length(v_points) NOT BETWEEN 1 AND 20000 THEN
        RAISE EXCEPTION 'NAV coordinate count outside bound';
    END IF;
    FOR v_point IN SELECT value FROM jsonb_array_elements(v_points) LOOP
        IF jsonb_typeof(v_point) IS DISTINCT FROM 'object'
           OR coalesce(v_point->>'date','') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
           OR coalesce(v_point->>'close','') !~ '^[0-9]+([.][0-9]+)?$' THEN
            RAISE EXCEPTION 'invalid NAV coordinate';
        END IF;
        v_day := (v_point->>'date')::date;
        v_value := (v_point->>'close')::numeric;
        IF v_day<DATE '1900-01-01' OR v_day>(v_now AT TIME ZONE 'Asia/Shanghai')::date
           OR v_previous IS NOT NULL AND v_day<=v_previous OR v_value<=0 THEN
            RAISE EXCEPTION 'future, unordered, duplicate or nonpositive NAV';
        END IF;
        v_previous := v_day;
    END LOOP;
    IF p_payload->>'asOf' IS DISTINCT FROM v_previous::text THEN
        RAISE EXCEPTION 'NAV asOf does not match the actual tail';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('fund-nav-sync:' || p_code,0));
    SELECT h.id INTO v_id FROM market.fund_nav_chart_histories h
      WHERE h.fund_code=p_code AND h.retrieved_at>=v_retrieved-interval '30 seconds'
        AND h.payload=p_payload ORDER BY h.retrieved_at DESC,h.id DESC LIMIT 1;
    IF v_id IS NULL THEN
        INSERT INTO market.fund_nav_chart_histories(fund_code,retrieved_at,available_at,payload)
          VALUES(p_code,v_retrieved,v_retrieved,p_payload) RETURNING id INTO v_id;
    END IF;
    RETURN v_id;
END;
$$;

REVOKE ALL ON FUNCTION market.append_fund_nav_sync_history(UUID,UUID,TEXT,JSONB)
    FROM PUBLIC,authenticated,wealthsail_worker;
GRANT EXECUTE ON FUNCTION market.append_fund_nav_sync_history(UUID,UUID,TEXT,JSONB)
    TO wealthsail_api;

COMMIT;
