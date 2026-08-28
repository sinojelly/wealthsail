-- =============================================================================
-- WealthSail Migration 024: password reset and authenticated tenant creation
-- =============================================================================

CREATE TABLE IF NOT EXISTS identity.password_reset_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_active
    ON identity.password_reset_tokens (user_id, expires_at DESC)
    WHERE consumed_at IS NULL;

ALTER TABLE identity.password_reset_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.password_reset_tokens FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS api_password_reset_access
    ON identity.password_reset_tokens;
CREATE POLICY api_password_reset_access
ON identity.password_reset_tokens
FOR ALL TO wealthsail_api
USING (true)
WITH CHECK (true);

REVOKE ALL ON identity.password_reset_tokens FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, UPDATE ON identity.password_reset_tokens TO wealthsail_api;

-- Consume exactly once, replace the password hash, invalidate all other reset
-- links and revoke every refresh session in the same transaction.
CREATE OR REPLACE FUNCTION identity.reset_password(
    p_token_hash TEXT,
    p_new_password_hash TEXT
)
RETURNS TABLE(out_user_id UUID, out_email TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, audit, pg_temp
AS $$
DECLARE
    v_user_id UUID;
    v_email TEXT;
BEGIN
    IF p_token_hash IS NULL OR length(p_token_hash) <> 64
       OR p_new_password_hash IS NULL OR p_new_password_hash = '' THEN
        RAISE EXCEPTION 'RESET_TOKEN_INVALID';
    END IF;

    SELECT prt.user_id
      INTO v_user_id
      FROM identity.password_reset_tokens prt
     WHERE prt.token_hash = p_token_hash
       AND prt.consumed_at IS NULL
       AND prt.expires_at > clock_timestamp()
     FOR UPDATE;

    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'RESET_TOKEN_INVALID';
    END IF;

    UPDATE identity.profiles
       SET password_hash = p_new_password_hash, updated_at = now()
     WHERE id = v_user_id
     RETURNING email INTO v_email;

    UPDATE identity.password_reset_tokens
       SET consumed_at = now()
     WHERE user_id = v_user_id AND consumed_at IS NULL;

    UPDATE identity.refresh_sessions
       SET revoked_at = COALESCE(revoked_at, now())
     WHERE user_id = v_user_id AND revoked_at IS NULL;

    INSERT INTO audit.audit_log(
        tenant_id, actor_type, actor_id, action, resource_type,
        resource_id, details
    )
    SELECT
        tm.tenant_id, 'USER', v_user_id, 'PASSWORD_RESET',
        'identity.profiles', v_user_id::TEXT,
        jsonb_build_object('sessionsRevoked', true)
    FROM identity.tenant_members tm
    WHERE tm.user_id = v_user_id AND tm.status = 'ACTIVE';

    RETURN QUERY SELECT v_user_id, v_email;
END;
$$;

REVOKE ALL ON FUNCTION identity.reset_password(TEXT, TEXT)
FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION identity.reset_password(TEXT, TEXT)
TO wealthsail_api;

-- Authenticated users may create an additional tenant only for themselves.
-- auth.uid() is supplied by the tenant transaction handshake.
CREATE OR REPLACE FUNCTION identity.create_tenant(
    p_actor_id UUID,
    p_name TEXT,
    p_base_currency TEXT DEFAULT 'CNY'
)
RETURNS TABLE(out_tenant_id UUID, out_tenant_name TEXT, out_role TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, audit, pg_temp
AS $$
DECLARE
    v_tenant_id UUID;
    v_name TEXT;
BEGIN
    IF p_actor_id IS NULL OR p_actor_id IS DISTINCT FROM auth.uid() THEN
        RAISE EXCEPTION 'TENANT_CREATE_FORBIDDEN';
    END IF;
    v_name := trim(p_name);
    IF length(v_name) < 2 OR length(v_name) > 64 THEN
        RAISE EXCEPTION 'TENANT_NAME_INVALID';
    END IF;
    IF upper(p_base_currency) !~ '^[A-Z]{3}$' THEN
        RAISE EXCEPTION 'BASE_CURRENCY_INVALID';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM identity.profiles WHERE id = p_actor_id
    ) THEN
        RAISE EXCEPTION 'TENANT_CREATE_FORBIDDEN';
    END IF;

    INSERT INTO identity.tenants(name, base_currency)
    VALUES (v_name, upper(p_base_currency))
    RETURNING id INTO v_tenant_id;

    INSERT INTO identity.tenant_members(tenant_id, user_id, role, status)
    VALUES (v_tenant_id, p_actor_id, 'OWNER', 'ACTIVE');

    INSERT INTO audit.audit_log(
        tenant_id, actor_type, actor_id, action, resource_type,
        resource_id, details
    )
    VALUES (
        v_tenant_id, 'USER', p_actor_id, 'TENANT_CREATED',
        'identity.tenants', v_tenant_id::TEXT,
        jsonb_build_object('baseCurrency', upper(p_base_currency))
    );

    RETURN QUERY SELECT v_tenant_id, v_name, 'OWNER'::TEXT;
END;
$$;

REVOKE ALL ON FUNCTION identity.create_tenant(UUID, TEXT, TEXT)
FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION identity.create_tenant(UUID, TEXT, TEXT)
TO wealthsail_api;
