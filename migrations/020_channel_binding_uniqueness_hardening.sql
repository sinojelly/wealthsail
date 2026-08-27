-- =============================================================================
-- WealthSail Migration 020: 企业微信绑定唯一性与跨用户冲突加固
--
-- 目标：
--   1. 同一 tenant/user/channel 最多保留一条 ACTIVE binding，避免同一用户重复推送；
--   2. 同一外部企业微信身份如果已经属于其他 tenant/user，必须拒绝第二次绑定，
--      不能把旧 binding_id 写进新用户的 challenge；
--   3. 报告仍按 tenant 内全部 ACTIVE binding 逐用户展开，不改变多用户投递语义。
--
-- 这是 019 之后的 forward-only 迁移。函数采用固定 search_path、显式撤销 PUBLIC
-- EXECUTE，并只授权 wealthsail_api，延续 011/013 的特权函数安全约定。
-- =============================================================================

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
    v_existing RECORD;
    v_binding_id UUID;
BEGIN
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
        RAISE EXCEPTION 'BINDING_TOKEN_EXHAUSTED';
    END IF;

    -- Lock a possible external-identity conflict before changing the user's
    -- existing bindings. The same external identity may be reactivated only
    -- for its original tenant/user owner.
    SELECT cb.id, cb.tenant_id, cb.user_id
      INTO v_existing
      FROM identity.channel_bindings cb
     WHERE cb.channel_type = v_challenge.channel_type
       AND cb.external_user_hash = p_external_user_hash
     FOR UPDATE;

    IF v_existing.id IS NOT NULL
       AND (v_existing.tenant_id <> v_challenge.tenant_id
            OR v_existing.user_id <> v_challenge.user_id) THEN
        RAISE EXCEPTION 'BINDING_EXTERNAL_ID_ALREADY_BOUND';
    END IF;

    -- Rebinding is an atomic replacement: revoke all prior ACTIVE bindings for
    -- this tenant/user/channel, then activate exactly the verified identity.
    UPDATE identity.channel_bindings
       SET status = 'REVOKED', revoked_at = now(), version = version + 1
     WHERE tenant_id = v_challenge.tenant_id
       AND user_id = v_challenge.user_id
       AND channel_type = v_challenge.channel_type
       AND status = 'ACTIVE';

    INSERT INTO identity.channel_bindings
        (tenant_id, user_id, channel_type, external_user_hash,
         external_user_ciphertext, external_username, status, bound_at,
         revoked_at)
    VALUES
        (v_challenge.tenant_id, v_challenge.user_id, v_challenge.channel_type,
         p_external_user_hash, p_external_user_ciphertext,
         p_external_username, 'ACTIVE', now(), NULL)
    ON CONFLICT (channel_type, external_user_hash) DO UPDATE
        SET status = 'ACTIVE',
            external_user_ciphertext = EXCLUDED.external_user_ciphertext,
            external_username = EXCLUDED.external_username,
            bound_at = now(),
            revoked_at = NULL,
            last_used_at = now(),
            version = channel_bindings.version + 1
        WHERE channel_bindings.tenant_id = EXCLUDED.tenant_id
          AND channel_bindings.user_id = EXCLUDED.user_id
    RETURNING id INTO v_binding_id;

    -- The WHERE predicate above is also a race-condition guard. A conflicting
    -- insert between SELECT and INSERT yields no updated row and therefore no
    -- binding id; fail the whole transaction rather than consume the challenge.
    IF v_binding_id IS NULL THEN
        RAISE EXCEPTION 'BINDING_EXTERNAL_ID_ALREADY_BOUND';
    END IF;

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

REVOKE ALL ON FUNCTION identity.consume_binding_challenge(TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.consume_binding_challenge(TEXT, TEXT, TEXT, TEXT)
    TO wealthsail_api;

COMMENT ON FUNCTION identity.consume_binding_challenge(TEXT, TEXT, TEXT, TEXT) IS
    'Atomically consumes a challenge, rejects cross-owner external identities, and leaves at most one ACTIVE binding per tenant/user/channel.';

-- Clean historical duplicate ACTIVE bindings before adding the invariant.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY tenant_id, user_id, channel_type
               ORDER BY created_at DESC, id DESC
           ) AS active_rank
      FROM identity.channel_bindings
     WHERE status = 'ACTIVE'
)
UPDATE identity.channel_bindings cb
   SET status = 'REVOKED', revoked_at = now(), version = version + 1
  FROM ranked r
 WHERE cb.id = r.id
   AND r.active_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_bindings_active_user_channel
    ON identity.channel_bindings (tenant_id, user_id, channel_type)
    WHERE status = 'ACTIVE';
