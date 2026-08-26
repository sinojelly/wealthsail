-- ==============================================================================
-- WealthSail 测试用户种子（在 Supabase SQL Editor 中整段执行）
-- 用途：插入测试用户「张明」+ 租户「张氏家办」+ OWNER 成员绑定
-- 目标库：Supabase（migrations 001-013 已全部执行）
-- 幂等：全部 ON CONFLICT DO NOTHING，可重复执行
--
-- 【表结构差异说明】（与原始草稿 SQL 的差异，已按最新代码修正）
--   1. 用户表是 identity.profiles（id, email, nickname, password_hash...），
--      不存在 identity.users；external_id / display_name 列也不存在
--   2. identity.tenants 主键列名是 id（不是 tenant_id）
--   3. identity.tenant_members 唯一约束为 (tenant_id, user_id)
-- 结构依据：migrations/002_identity_and_tenancy.sql + 012 + 013，
--          种子写法对齐 seeds/dev/006_mock_seed_data.sql
-- ==============================================================================

-- 1. 插入用户（identity.profiles）
--    注意：profile.id 必须与 Supabase auth.users.id 一致，才能走 Supabase Auth 登录；
--    仅后端密码登录（/auth/login 读 password_hash）不依赖 auth.users。
INSERT INTO identity.profiles (id, email, nickname)
VALUES ('11111111-1111-1111-1111-111111111111', 'zhangming@example.com', '张明')
ON CONFLICT (id) DO NOTHING;

-- 2. 插入租户（identity.tenants；status 有默认值 'ACTIVE'，可不写）
INSERT INTO identity.tenants (id, name, base_currency)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '张氏家办', 'CNY')
ON CONFLICT (id) DO NOTHING;

-- 3. 绑定成员（identity.tenant_members；role 默认 'OWNER'，status 默认 'ACTIVE'）
INSERT INTO identity.tenant_members (tenant_id, user_id, role, status)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'OWNER', 'ACTIVE')
ON CONFLICT (tenant_id, user_id) DO NOTHING;

-- （可选）设置 bcrypt 密码 demo1234，使后端 /auth/login 可直接登录。
-- 需要 pgcrypto 扩展（Supabase 自带）。
-- UPDATE identity.profiles
--    SET password_hash = crypt('demo1234', gen_salt('bf'))
--  WHERE id = '11111111-1111-1111-1111-111111111111'
--    AND password_hash IS NULL;
