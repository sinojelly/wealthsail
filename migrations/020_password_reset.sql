-- ==============================================================================
-- WealthSail Migration 020: 密码重置令牌与租户自助创建
--
-- 补全 modules/identity 领域服务所需的两个能力（幂等可重跑）：
--   1. identity.password_reset_tokens —— 一次性密码重置令牌（仅存 HMAC 摘要）
--   2. identity.create_tenant —— SECURITY DEFINER 租户自助创建（创建租户 + OWNER 成员）
--   3. identity.reset_password —— SECURITY DEFINER 重置密码（校验令牌、更新哈希、
--      撤销该用户全部 refresh sessions、原子消费令牌）
--
-- 设计对齐既有约定：
--   - 令牌明文永不落库（与 binding_challenges / invitations 相同，仅存 HMAC 摘要）
--   - RLS 采用 refresh_sessions 的 api_*_access 模式（FOR ALL TO wealthsail_api，
--     密码重置令牌是授权能力，非凭据材料）
--   - profiles.password_hash 对浏览器角色 012 已 REVOKE SELECT；重置密码必须走
--     SECURITY DEFINER（无登录态时 auth.uid() 为 NULL，普通 UPDATE 会被 RLS 拒绝）
-- ==============================================================================

-- ----------------------------------------------------------------------------
-- 1. 密码重置令牌表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS identity.password_reset_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at),
    CHECK ((consumed_at IS NULL) OR (consumed_at >= created_at))
);

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_lookup
    ON identity.password_reset_tokens (token_hash, expires_at)
    WHERE consumed_at IS NULL;

COMMENT ON TABLE identity.password_reset_tokens IS
    '一次性密码重置令牌（仅存 HMAC 摘要）；消费或过期后不可复用。';

ALTER TABLE identity.password_reset_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.password_reset_tokens FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS api_password_reset_access ON identity.password_reset_tokens;
CREATE POLICY api_password_reset_access ON identity.password_reset_tokens
    FOR ALL TO wealthsail_api USING (true) WITH CHECK (true);
REVOKE ALL ON identity.password_reset_tokens FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, UPDATE ON identity.password_reset_tokens TO wealthsail_api;

-- ----------------------------------------------------------------------------
-- 2. 租户自助创建（SECURITY DEFINER）
--    调用前提：调用者必须已通过身份认证（Service 层校验 access token）。
--    同名租户在同一用户下幂等：已存在同名活动租户时直接返回已有租户。
-- ----------------------------------------------------------------------------
DROP FUNCTION IF EXISTS identity.create_tenant(UUID, TEXT, CHAR(3));
CREATE FUNCTION identity.create_tenant(
    p_owner_user_id UUID,
    p_name TEXT,
    p_base_currency CHAR(3) DEFAULT 'CNY'
)
RETURNS TABLE (out_tenant_id UUID, out_tenant_name TEXT, out_role TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, pg_temp
AS $$
DECLARE
    v_tenant_id UUID;
    v_clean_name TEXT;
BEGIN
    v_clean_name := btrim(p_name);
    IF v_clean_name IS NULL OR length(v_clean_name) < 2 OR length(v_clean_name) > 64 THEN
        RAISE EXCEPTION 'TENANT_NAME_INVALID'
            USING DETAIL = '租户名称长度必须在 2 到 64 个字符之间';
    END IF;

    -- 同名活动租户幂等返回（仅限本人已加入的租户）
    SELECT tm.tenant_id INTO v_tenant_id
    FROM tenant_members tm
    JOIN tenants t ON t.id = tm.tenant_id
    WHERE tm.user_id = p_owner_user_id
      AND t.status = 'ACTIVE'
      AND btrim(t.name) = v_clean_name
    ORDER BY tm.created_at ASC
    LIMIT 1;

    IF v_tenant_id IS NULL THEN
        INSERT INTO tenants (id, name, base_currency)
        VALUES (gen_random_uuid(), v_clean_name, p_base_currency)
        RETURNING id INTO v_tenant_id;

        INSERT INTO tenant_members (tenant_id, user_id, role, status)
        VALUES (v_tenant_id, p_owner_user_id, 'OWNER', 'ACTIVE');
    END IF;

    RETURN QUERY
        SELECT t.id, t.name, tm.role
        FROM tenants t
        JOIN tenant_members tm ON tm.tenant_id = t.id
        WHERE t.id = v_tenant_id AND tm.user_id = p_owner_user_id;
END;
$$;

COMMENT ON FUNCTION identity.create_tenant(UUID, TEXT, CHAR(3)) IS
    '创建新租户并将调用用户绑定为 OWNER；同名活动租户幂等返回已有租户。';

-- ----------------------------------------------------------------------------
-- 3. 密码重置（SECURITY DEFINER）
--    原子执行：锁定并校验令牌 → 更新 password_hash → 撤销该用户全部
--    refresh sessions → 标记令牌 CONSUMED。任一步失败整体回滚。
-- ----------------------------------------------------------------------------
DROP FUNCTION IF EXISTS identity.reset_password(TEXT, TEXT);
CREATE FUNCTION identity.reset_password(
    p_token_hash TEXT,
    p_new_password_hash TEXT
)
RETURNS TABLE (out_user_id UUID, out_email TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, pg_temp
AS $$
DECLARE
    v_reset RECORD;
BEGIN
    SELECT prt.id, prt.user_id, prt.expires_at
      INTO v_reset
      FROM password_reset_tokens prt
     WHERE prt.token_hash = p_token_hash
       AND prt.consumed_at IS NULL
       AND prt.expires_at > now()
       FOR UPDATE OF prt;

    IF v_reset.id IS NULL THEN
        RAISE EXCEPTION 'RESET_TOKEN_INVALID'
            USING DETAIL = '重置令牌无效、已使用或已过期';
    END IF;

    UPDATE profiles
       SET password_hash = p_new_password_hash, updated_at = now()
     WHERE id = v_reset.user_id;

    -- 安全原则：密码重置后所有既有会话立即失效
    UPDATE refresh_sessions
       SET revoked_at = COALESCE(revoked_at, now())
     WHERE user_id = v_reset.user_id AND revoked_at IS NULL;

    UPDATE password_reset_tokens
       SET consumed_at = now()
     WHERE id = v_reset.id;

    RETURN QUERY
        SELECT p.id, p.email
        FROM profiles p
        WHERE p.id = v_reset.user_id;
END;
$$;

COMMENT ON FUNCTION identity.reset_password(TEXT, TEXT) IS
    '校验并消费一次性重置令牌，更新密码哈希，并撤销该用户全部刷新会话。';
