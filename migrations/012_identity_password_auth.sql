-- =============================================================================
-- 012: 真实密码认证字段（bcrypt）
--   * identity.profiles 增加 password_hash（bcrypt，仅存哈希，明文永不落库）
--   * 不包含演示用户、默认口令或可被浏览器角色调用的哈希查询 RPC
--   * 开发数据必须从 seeds/dev 独立加载
-- =============================================================================

ALTER TABLE identity.profiles
    ADD COLUMN IF NOT EXISTS password_hash TEXT;

COMMENT ON COLUMN identity.profiles.password_hash IS
    'bcrypt 口令哈希（只读，禁止明文存储）；NULL 表示该用户未设置口令（如 Supabase Auth 托管）。';

-- Upgrade safety: remove the legacy hash-returning RPC if an older checkout
-- installed it. Login now queries through the private API database role.
DROP FUNCTION IF EXISTS identity.authenticate_credentials(TEXT);

REVOKE SELECT (password_hash) ON identity.profiles FROM PUBLIC, authenticated;
