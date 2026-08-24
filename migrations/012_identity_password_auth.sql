-- =============================================================================
-- 012: 真实密码认证（bcrypt）
--   * identity.profiles 增加 password_hash（bcrypt，仅存哈希，明文永不落库）
--   * 种子用户初始化口令：demo1234（开发/演示用途，生产环境必须改密）
--   * authenticate_credentials 增加 password_hash 输出列（行类型变更，
--     先 DROP 再 CREATE，与 011 的 consume_binding_challenge 同策略）
-- =============================================================================

ALTER TABLE identity.profiles
    ADD COLUMN IF NOT EXISTS password_hash TEXT;

COMMENT ON COLUMN identity.profiles.password_hash IS
    'bcrypt 口令哈希（只读，禁止明文存储）；NULL 表示该用户未设置口令（如 Supabase Auth 托管）。';

-- 种子用户初始化口令（bcrypt('demo1234')，每用户独立盐）
UPDATE identity.profiles
SET password_hash = '$2b$12$29RZ8SC25DuEagNAib5hiuTiGqa8KvEVEUcX1E58OORyUTYO/QI76'
WHERE email = 'zhangming@example.com' AND password_hash IS NULL;

UPDATE identity.profiles
SET password_hash = '$2b$12$xqyTqQnQZNhlWNIjLdMs4eBMifCVwXULRqyTEfewrvN9eebW2jcee'
WHERE email = 'lihua@example.com' AND password_hash IS NULL;

UPDATE identity.profiles
SET password_hash = '$2b$12$Qqc02Vx6bcYnNmgECIieSOAyK3d25j4L0ExV/e3Z.xXGdHWofWoB.'
WHERE email = 'wangfang@example.com' AND password_hash IS NULL;

-- 认证助手：email → 档案 + 口令哈希 + 活动租户
DROP FUNCTION IF EXISTS identity.authenticate_credentials(TEXT);
CREATE FUNCTION identity.authenticate_credentials(p_email TEXT)
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

-- 注册流程（register service）经 worker 池（system_connection）写入：
--   profiles / tenants / tenant_members / accounts 需要 INSERT 权限。
GRANT INSERT, SELECT, UPDATE ON identity.profiles TO wealthsail_worker;
GRANT INSERT, SELECT ON identity.tenants, identity.tenant_members, identity.accounts
    TO wealthsail_worker;

REVOKE ALL ON FUNCTION identity.authenticate_credentials(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.authenticate_credentials(TEXT)
    TO authenticated, wealthsail_user;

COMMENT ON FUNCTION identity.authenticate_credentials(TEXT) IS
    'Privileged email lookup for the login service; returns profile, bcrypt hash and active tenant memberships.';
