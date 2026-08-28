-- Atomic self-service registration: create profile, tenant and OWNER membership.
CREATE OR REPLACE FUNCTION identity.register_with_tenant(
    p_email TEXT,
    p_nickname TEXT,
    p_password_hash TEXT,
    p_tenant_name TEXT,
    p_base_currency TEXT DEFAULT 'CNY'
)
RETURNS TABLE(out_user_id UUID, out_tenant_id UUID, out_tenant_name TEXT, out_role TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, audit, pg_temp
AS $$
DECLARE
    v_user_id UUID;
    v_tenant_id UUID;
BEGIN
    IF p_email IS NULL OR position('@' IN p_email) = 0 THEN
        RAISE EXCEPTION 'EMAIL_INVALID';
    END IF;
    IF length(trim(p_tenant_name)) < 2 OR length(trim(p_tenant_name)) > 64 THEN
        RAISE EXCEPTION 'TENANT_NAME_INVALID';
    END IF;
    IF upper(p_base_currency) !~ '^[A-Z]{3}$' THEN
        RAISE EXCEPTION 'BASE_CURRENCY_INVALID';
    END IF;

    INSERT INTO identity.profiles(email, nickname, password_hash)
    VALUES (lower(trim(p_email)), trim(p_nickname), p_password_hash)
    RETURNING id INTO v_user_id;

    INSERT INTO identity.tenants(name, base_currency)
    VALUES (trim(p_tenant_name), upper(p_base_currency))
    RETURNING id INTO v_tenant_id;

    INSERT INTO identity.tenant_members(tenant_id, user_id, role, status)
    VALUES (v_tenant_id, v_user_id, 'OWNER', 'ACTIVE');

    INSERT INTO audit.audit_log(
        tenant_id, actor_type, actor_id, action, resource_type,
        resource_id, details
    )
    VALUES (
        v_tenant_id, 'USER', v_user_id, 'SELF_REGISTERED',
        'identity.tenants', v_tenant_id::TEXT,
        jsonb_build_object('email', lower(trim(p_email)))
    );

    RETURN QUERY SELECT v_user_id, v_tenant_id, trim(p_tenant_name), 'OWNER'::TEXT;
EXCEPTION
    WHEN unique_violation THEN
        RAISE EXCEPTION 'EMAIL_ALREADY_REGISTERED';
END;
$$;

REVOKE ALL ON FUNCTION identity.register_with_tenant(TEXT, TEXT, TEXT, TEXT, TEXT)
FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION identity.register_with_tenant(TEXT, TEXT, TEXT, TEXT, TEXT)
TO wealthsail_api;
