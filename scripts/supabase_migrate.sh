#!/usr/bin/env bash
# ==============================================================================
# WealthSail → Supabase 一键迁移脚本
#   * 预建角色：wealthsail_user（API 池角色引用）、wealthsail_worker（BYPASSRLS，
#     worker 池语义）；GRANT authenticated TO 两者（SET LOCAL ROLE 需要）。
#   * 按序应用 migrations/001..012（纯 SQL 迁移体系，项目无 Alembic）。
#   * 建 public.profiles 兼容表（带 email 列，提供 risk_level 供双参数函数关联）。
#   * 双参数重载 identity.authenticate_credentials(email, password)：
#     SQL 内 bcrypt 校验（pgcrypto，$2a$/$2b$/$2y$ 统一 $2a$ 前缀），
#     返回 user_id/nickname/risk_level（risk_level 关联 public.profiles）。
#
# 用法：
#   DSN="postgresql://postgres.xxx@aws-...pooler.supabase.com:6543/postgres?sslmode=require" \
#     ./scripts/supabase_migrate.sh
#   不传 DSN 时自动读取 .env 的 DATABASE_URL。
# 注意：连接角色必须是超级用户（Supabase 的 postgres 角色），
#       才能 CREATE ROLE ... BYPASSRLS 与 SECURITY DEFINER 函数。
# ==============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

DSN="${DSN:-$(grep '^DATABASE_URL=' .env | head -1 | cut -d= -f2- | tr -d '"')}"
if [[ -z "$DSN" ]]; then
    echo "ERROR: 未找到 DATABASE_URL（可用 DSN=... 传入）" >&2
    exit 1
fi

# psql/libpq 不识别 asyncpg 专用参数 statement_cache_size，剥离后再用
DSN_CLEAN="${DSN/?statement_cache_size=0/}"
DSN_CLEAN="${DSN_CLEAN/&statement_cache_size=0/}"

PSQL=(psql "$DSN_CLEAN" -v ON_ERROR_STOP=1 -q -X)

echo "==> 1/5 连通性检查"
"${PSQL[@]}" -c "SELECT current_user, current_database(), version();" >/dev/null
echo "    连接成功"

echo "==> 2/5 预建角色（幂等）"
"${PSQL[@]}" <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wealthsail_user') THEN
        CREATE ROLE wealthsail_user LOGIN PASSWORD 'dev_password_123';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wealthsail_worker') THEN
        CREATE ROLE wealthsail_worker LOGIN BYPASSRLS PASSWORD 'dev_worker_password_123';
    END IF;
END $$;
-- SET LOCAL ROLE authenticated 需要角色成员关系（本地 compat 脚本同款）
GRANT authenticated TO wealthsail_user, wealthsail_worker;
SQL

echo "==> 3/5 应用 migrations/001..012（SKIP_MIGRATIONS=1 跳过已应用库）"
if [[ -z "${SKIP_MIGRATIONS:-}" ]]; then
    for f in migrations/00{1,2,3,4,5,6,7,8,9}_*.sql migrations/01{0,1,2}_*.sql; do
        [[ -f "$f" ]] || continue
        echo "    - $f"
        "${PSQL[@]}" -f "$f"
    done
else
    echo "    跳过（已应用）"
fi

echo "==> 4/5 public.profiles 兼容表（risk_level 来源）"
"${PSQL[@]}" <<'SQL'
-- Supabase 上可能已存在旧版 public.profiles（无 email 列、FK→auth.users）：
-- 幂等补齐 email 列，先插 auth.users 满足外键，再插测试用户。
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS email TEXT;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'profiles_email_key' AND conrelid = 'public.profiles'::regclass
    ) THEN
        ALTER TABLE public.profiles ADD CONSTRAINT profiles_email_key UNIQUE (email);
    END IF;
END $$;

-- auth.users 占位（满足 profiles_id_fkey；encrypted_password 为 Supabase Auth 格式 bcrypt）
INSERT INTO auth.users
    (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
     raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
VALUES
    ('00000000-0000-0000-0000-000000000000', '11111111-1111-1111-1111-111111111111',
     'authenticated', 'authenticated', 'zhangming@example.com',
     crypt('demo1234', gen_salt('bf')), now(),
     '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()),
    ('00000000-0000-0000-0000-000000000000', '22222222-2222-2222-2222-222222222222',
     'authenticated', 'authenticated', 'lihua@example.com',
     crypt('demo1234', gen_salt('bf')), now(),
     '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()),
    ('00000000-0000-0000-0000-000000000000', '33333333-3333-3333-3333-333333333333',
     'authenticated', 'authenticated', 'wangfang@example.com',
     crypt('demo1234', gen_salt('bf')), now(),
     '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now())
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.profiles (id, email, nickname, risk_level) VALUES
    ('11111111-1111-1111-1111-111111111111', 'zhangming@example.com', '张明', 'balanced'),
    ('22222222-2222-2222-2222-222222222222', 'lihua@example.com',    '李华', 'aggressive'),
    ('33333333-3333-3333-3333-333333333333', 'wangfang@example.com', '王芳', 'conservative')
ON CONFLICT (id) DO NOTHING;
SQL

echo "==> 5/5 登录函数（单参数=后端契约 / 双参数=SQL 内 bcrypt 校验）"
"${PSQL[@]}" <<'SQL'
-- 单参数：后端 /api/v1/auth/login 契约（迁移 012 已建，此处幂等确保）
CREATE OR REPLACE FUNCTION identity.authenticate_credentials(p_email TEXT)
RETURNS TABLE (
    user_id UUID, email TEXT, nickname TEXT, password_hash TEXT,
    tenant_id UUID, tenant_name TEXT, tenant_role TEXT
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = identity, pg_catalog
AS $$
    SELECT p.id, p.email, p.nickname, p.password_hash,
           tm.tenant_id, t.name, tm.role
    FROM identity.profiles p
    JOIN identity.tenant_members tm ON tm.user_id = p.id AND tm.status = 'ACTIVE'
    JOIN identity.tenants t ON t.id = tm.tenant_id
    WHERE lower(p.email) = lower(p_email)
$$;

-- 双参数重载：SQL 内 bcrypt 校验，返回 user_id/nickname/risk_level
CREATE OR REPLACE FUNCTION identity.authenticate_credentials(p_email TEXT, p_password TEXT)
RETURNS TABLE (user_id UUID, nickname TEXT, risk_level TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
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

    IF v_user_id IS NULL OR v_password_hash IS NULL THEN
        RETURN;  -- 无此用户 / 未设置口令 → 空结果（不泄露账号存在性）
    END IF;

    v_prefix := left(v_password_hash, 4);
    IF v_prefix NOT IN ('$2a$', '$2b$', '$2y$') THEN
        RETURN;  -- 非 bcrypt 哈希（如 Supabase Auth 托管）→ 空结果
    END IF;

    -- pgcrypto 只认 $2a$ 前缀；对 ASCII 口令 $2b$/$2y$ 与 $2a$ 摘要等价，
    -- 统一改写前缀校验；crypt() 返回值同样带 $2a$ 前缀，比较目标用改写串。
    -- （Supabase 的 pgcrypto 装在 extensions schema，此处 schema 限定调用）
    v_ok := (extensions.crypt(p_password, '$2a$' || substring(v_password_hash from 5))
             = ('$2a$' || substring(v_password_hash from 5)));
    IF v_ok IS DISTINCT FROM TRUE THEN
        RETURN;  -- 口令错误
    END IF;

    RETURN QUERY
    SELECT p.id, p.nickname, pp.risk_level::TEXT
      FROM identity.profiles p
      LEFT JOIN public.profiles pp ON lower(pp.email) = lower(p.email)
     WHERE lower(p.email) = lower(p_email);
END;
$$;

REVOKE ALL ON FUNCTION identity.authenticate_credentials(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION identity.authenticate_credentials(TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.authenticate_credentials(TEXT)
    TO authenticated, wealthsail_user;
GRANT EXECUTE ON FUNCTION identity.authenticate_credentials(TEXT, TEXT)
    TO authenticated, wealthsail_user;
SQL

echo "==> 验证"
"${PSQL[@]}" <<'SQL'
SELECT count(*) AS identity_profiles FROM identity.profiles;
SELECT count(*) AS public_profiles FROM public.profiles;
SELECT user_id, nickname, tenant_name, tenant_role
  FROM identity.authenticate_credentials('zhangming@example.com');
SELECT user_id, nickname, risk_level
  FROM identity.authenticate_credentials('zhangming@example.com', 'demo1234');
SELECT count(*) AS wrong_pw_rows
  FROM identity.authenticate_credentials('zhangming@example.com', 'wrong');
SQL

echo "==> 完成。后端启动："
echo "    source .venv/bin/activate && uvicorn backend.main:app --host 0.0.0.0 --port 8000"
