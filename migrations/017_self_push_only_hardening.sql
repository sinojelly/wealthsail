-- ==============================================================================
-- WealthSail Migration 017: 推送仅面向绑定用户本人（self-push-only hardening）
--
-- 背景：报告推送的收件人在写入端由
--   backend/reports/repository.py:insert_report_outbox_deliveries 决定——
--   只给 (tenant_id, user_id) 且 channel_type='wecom' 且 status='ACTIVE'
--   的绑定写 outbox（不 fan-out 到租户内其他成员/绑定）。消费端
--   backend/jobs/outbox.py 亦 fail-closed：binding 必须存在、tenant 匹配、
--   ACTIVE、且有加密路由，否则拒绝（重试直至 DEAD）。
--
-- 本迁移修复一个机制漏洞：identity.consume_binding_challenge 旧实现只按
--   UNIQUE(channel_type, external_user_hash) upsert——同一企微 userid 重绑
--   会复用旧行，但**换绑到不同企微 userid 时旧 ACTIVE 绑定原样保留**，
--   一个用户+租户+渠道可同时存在多条 ACTIVE 绑定，导致同一报告被写入
--   多条 outbox（重复推送）。
--
-- 修复：
--   1) 重定义 consume_binding_challenge：消费挑战前（同一事务内）先撤销
--      同租户同用户同渠道的既有 ACTIVE 绑定，再 upsert 本次绑定；
--   2) 部分唯一索引兜底：同 (tenant_id, user_id, channel_type) 至多一条
--      ACTIVE，从数据库层面杜绝重复推送目标。
-- ==============================================================================

-- 1. 换绑时原子撤销旧 ACTIVE 绑定（INSERT 前 UPDATE，同一事务）
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

    -- 换绑/重绑：先撤销该用户在该租户该渠道的所有既有 ACTIVE 绑定。
    -- 若 external_user_hash 命中的行本身是 ACTIVE，也会先被撤销，
    -- 随后的 ON CONFLICT DO UPDATE 再将其置回 ACTIVE——最终仍只有一条 ACTIVE。
    UPDATE identity.channel_bindings
       SET status = 'REVOKED', revoked_at = now(), version = version + 1
     WHERE tenant_id = v_challenge.tenant_id
       AND user_id = v_challenge.user_id
       AND channel_type = v_challenge.channel_type
       AND status = 'ACTIVE';

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
    'Atomically consumes a one-time binding challenge, revokes the user''s prior ACTIVE bindings for the same tenant+channel, and upserts the channel binding (at most one ACTIVE per tenant+user+channel).';

-- 2. 历史脏数据自愈 + 部分唯一索引兜底：
--    同 (tenant_id, user_id, channel_type) 仅保留最新一条 ACTIVE，
--    其余置 REVOKED（幂等：无重复 ACTIVE 时影响 0 行；随后索引创建成功）。
UPDATE identity.channel_bindings cb
   SET status = 'REVOKED', revoked_at = now(), version = version + 1
 WHERE cb.status = 'ACTIVE'
   AND cb.id NOT IN (
       SELECT DISTINCT ON (tenant_id, user_id, channel_type) id
         FROM identity.channel_bindings
        WHERE status = 'ACTIVE'
        ORDER BY tenant_id, user_id, channel_type, created_at DESC
   );

--    部分唯一索引：同 (tenant_id, user_id, channel_type) 至多一条 ACTIVE。
CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_bindings_active_user_channel
    ON identity.channel_bindings (tenant_id, user_id, channel_type)
    WHERE status = 'ACTIVE';
