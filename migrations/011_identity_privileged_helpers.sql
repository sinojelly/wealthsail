-- ==============================================================================
-- WealthSail Migration 011: Identity Privileged Helpers (SECURITY DEFINER)
-- Version: v2.2.0 (local MVP bridge)
-- Description: Login and WeCom binding consumption are privileged identity
--              operations. In Supabase these run inside the auth service; on
--              local Postgres they are exposed as SECURITY DEFINER functions
--              owned by the migration superuser (same semantics as Supabase
--              migrations applied by `postgres`). The API never reads raw
--              credential material through RLS.
--
--              consume_binding_challenge: atomic single-use challenge
--              consumption + channel_binding upsert (attempt-capped,
--              forward-only, trigger-guarded).
-- ==============================================================================

DROP FUNCTION IF EXISTS identity.authenticate_credentials(TEXT);

ALTER TABLE identity.channel_bindings
    ADD COLUMN IF NOT EXISTS external_user_ciphertext TEXT;

-- Re-creatable: the OUT-parameter row type may change between revisions.
DROP FUNCTION IF EXISTS identity.consume_binding_challenge(TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS identity.consume_binding_challenge(TEXT, TEXT, TEXT, TEXT);
CREATE FUNCTION identity.consume_binding_challenge(
    p_token_hash TEXT,
    p_external_user_hash TEXT,
    p_external_user_ciphertext TEXT,
    p_external_username TEXT
)
RETURNS TABLE (
    out_binding_id UUID,
    out_tenant_id UUID,
    out_user_id UUID,
    out_channel_type TEXT,
    out_status TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, pg_catalog
AS $$
DECLARE
    v_challenge RECORD;
    v_binding_id UUID;
BEGIN
    -- Atomically lock the one pending challenge for this token hash.
    -- NOTE: out-parameter names must never collide with column names used
    -- unqualified inside the body (plpgsql variable shadowing).
    SELECT bc.id, bc.tenant_id, bc.user_id, bc.channel_type,
           bc.attempt_count, bc.max_attempts
      INTO v_challenge
      FROM identity.binding_challenges bc
     WHERE bc.token_hash = p_token_hash
       AND bc.status = 'PENDING'
       AND bc.expires_at > now()
     FOR UPDATE;

    IF v_challenge.id IS NULL THEN
        RAISE EXCEPTION 'BINDING_TOKEN_INVALID';
    END IF;

    IF v_challenge.attempt_count + 1 > v_challenge.max_attempts THEN
        UPDATE identity.binding_challenges
           SET status = 'EXPIRED', resolved_at = now(),
               attempt_count = attempt_count + 1,
               version = version + 1
         WHERE id = v_challenge.id;
        RAISE EXCEPTION 'BINDING_TOKEN_EXHAUSTED';
    END IF;

    -- Upsert the verified channel binding (hash is the lookup key).
    INSERT INTO identity.channel_bindings
        (tenant_id, user_id, channel_type, external_user_hash, external_user_ciphertext,
         external_username, status, bound_at)
    VALUES
        (v_challenge.tenant_id, v_challenge.user_id, v_challenge.channel_type,
         p_external_user_hash, p_external_user_ciphertext,
         p_external_username, 'ACTIVE', now())
    ON CONFLICT (channel_type, external_user_hash) DO UPDATE
        SET status = 'ACTIVE',
            external_user_ciphertext = EXCLUDED.external_user_ciphertext,
            bound_at = now(), last_used_at = now(),
            version = channel_bindings.version + 1
    RETURNING id INTO v_binding_id;

    -- Consume the challenge in the same transaction (single-use, atomic).
    UPDATE identity.binding_challenges
       SET status = 'CONSUMED', consumed_at = now(), resolved_at = now(),
           binding_id = v_binding_id,
           attempt_count = attempt_count + 1,
           version = version + 1
     WHERE id = v_challenge.id;

    RETURN QUERY
        SELECT cb.id, cb.tenant_id, cb.user_id, cb.channel_type, cb.status
          FROM identity.channel_bindings cb
         WHERE cb.id = v_binding_id;
END;
$$;

REVOKE ALL ON FUNCTION identity.consume_binding_challenge(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.consume_binding_challenge(TEXT, TEXT, TEXT, TEXT)
    TO wealthsail_api;

COMMENT ON FUNCTION identity.consume_binding_challenge(TEXT, TEXT, TEXT, TEXT) IS
    'Atomically consumes a one-time binding challenge and upserts the channel binding.';
