-- =============================================================================
-- 修复登录：identity.authenticate_credentials
--   * 单参数版本 (p_email) —— 后端 /api/v1/auth/login 实际调用
--     （backend/identity/service.py），返回 profile + bcrypt 哈希 + 活动租户，
--     密码校验在应用层（Python bcrypt，支持 $2b$）。
--   * 双参数版本 (p_email, p_password) —— 兼容手工验收与旧调用方：
--     SQL 内用 pgcrypto 校验 bcrypt（$2a$/$2b$/$2y$ 统一按 $2a$ 前缀校验），
--     成功后返回 user_id / nickname / risk_level
--     （risk_level 关联遗留 public.profiles，无则 NULL；
--      新架构风险约束见 risk.investment_policies）。
--   * 两版本均 SECURITY DEFINER（owner=lenovo）以穿透 FORCE RLS。
--   幂等：可重复执行。
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS identity;

-- ---------- 单参数版本（项目后端契约，勿改签名） ----------
CREATE OR REPLACE FUNCTION identity.authenticate_credentials(p_email TEXT)
RETURNS TABLE (
    user_id UUID,
    email TEXT,
    nickname TEXT,
    password_hash TEXT,
    tenant_id UUID,
    tenant_name TEXT,
    tenant_role TEXT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = identity, pg_catalog
AS $$
    SELECT p.id, p.email, p.nickname, p.password_hash,
           tm.tenant_id, t.name, tm.role
    FROM identity.profiles p
    JOIN identity.tenant_members tm
      ON tm.user_id = p.id AND tm.status = 'ACTIVE'
    JOIN identity.tenants t ON t.id = tm.tenant_id
    WHERE lower(p.email) = lower(p_email)
$$;

-- ---------- 双参数重载（SQL 内校验 bcrypt，兼容验收/旧调用方） ----------
CREATE OR REPLACE FUNCTION identity.authenticate_credentials(p_email TEXT, p_password TEXT)
RETURNS TABLE (user_id UUID, nickname TEXT, risk_level TEXT)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = identity, pg_catalog
AS $$
DECLARE
    v_user_id UUID;
    v_nickname TEXT;
    v_password_hash TEXT;
    v_prefix TEXT;
    v_ok BOOLEAN;
BEGIN
    SELECT p.id, p.nickname, p.password_hash
      INTO v_user_id, v_nickname, v_password_hash
      FROM identity.profiles p
     WHERE lower(p.email) = lower(p_email);

    -- 无此用户 / 未设置口令 → 空结果（不泄露账号是否存在）
    IF v_user_id IS NULL OR v_password_hash IS NULL THEN
        RETURN;
    END IF;

    -- 非 bcrypt 哈希（如 Supabase Auth 托管）→ 空结果
    v_prefix := left(v_password_hash, 4);
    IF v_prefix NOT IN ('$2a$', '$2b$', '$2y$') THEN
        RETURN;
    END IF;

    -- pgcrypto 的 crypt() 只认 $2a$ 前缀；对 ASCII 口令 $2b$/$2y$ 与 $2a$
    -- 摘要等价，统一改写前缀后校验。crypt() 返回的哈希带 $2a$ 前缀，
    -- 因此比较目标也用改写后的 $2a$ 串（避免与存储的 $2b$ 前缀比较不等）。
    v_ok := (public.crypt(p_password, '$2a$' || substring(v_password_hash from 5))
             = ('$2a$' || substring(v_password_hash from 5)));
    IF v_ok IS DISTINCT FROM TRUE THEN
        RETURN;  -- 口令错误或校验失败 → 空结果
    END IF;

    -- 恒返回一行；risk_level 关联遗留 public.profiles（无则 NULL）
    RETURN QUERY
    SELECT v_user_id, v_nickname, pp.risk_level::TEXT
      FROM (VALUES (1)) AS d(x)
      LEFT JOIN public.profiles pp ON pp.id = v_user_id;
END;
$$;

REVOKE ALL ON FUNCTION identity.authenticate_credentials(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION identity.authenticate_credentials(TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.authenticate_credentials(TEXT)
    TO authenticated, wealthsail_user;
GRANT EXECUTE ON FUNCTION identity.authenticate_credentials(TEXT, TEXT)
    TO authenticated, wealthsail_user;

COMMENT ON FUNCTION identity.authenticate_credentials(TEXT) IS
    'Privileged email lookup for the login service; returns profile, bcrypt hash and active tenant memberships.';
COMMENT ON FUNCTION identity.authenticate_credentials(TEXT, TEXT) IS
    'SQL-side bcrypt verification; returns user_id/nickname/risk_level (risk_level from legacy public.profiles, NULL when absent).';
