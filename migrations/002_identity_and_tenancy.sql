-- ==============================================================================
-- WealthSail Migration 002: Identity, Tenancy & Accounts
-- Version: v2.0.0
-- Description: 租户、用户画像、多端渠道绑定与账户模型
-- ==============================================================================

-- 1. 租户表 (Tenants)
CREATE TABLE IF NOT EXISTS identity.tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'CLOSED')),
    base_currency CHAR(3) NOT NULL DEFAULT 'CNY',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. 用户画像表 (Profiles - 与认证系统 subject 一一映射)
-- Supabase profile.id MUST equal auth.users.id. The fixture rows in migration
-- 006 are database-only examples and are not login-ready Auth identities.
CREATE TABLE IF NOT EXISTS identity.profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE,
    nickname TEXT NOT NULL,
    avatar_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. 租户成员关系表 (Tenant Members)
CREATE TABLE IF NOT EXISTS identity.tenant_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'OWNER' CHECK (role IN ('OWNER', 'ADMIN', 'EDITOR', 'VIEWER')),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INVITED', 'SUSPENDED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id)
);

-- 4. 账户表 (Accounts - 券商/销售平台/手工账户)
CREATE TABLE IF NOT EXISTS identity.accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_type TEXT NOT NULL CHECK (account_type IN ('BROKERAGE', 'FUND_PLATFORM', 'MANUAL')),
    name TEXT NOT NULL,
    base_currency CHAR(3) NOT NULL DEFAULT 'CNY',
    provider_ref TEXT, -- 外部账户脱敏引用 (严禁落明文账号)
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CLOSED')),
    opened_on DATE NOT NULL DEFAULT CURRENT_DATE,
    closed_on DATE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id)
);

-- 5. 多端即时通讯渠道绑定表 (Channel Bindings)
CREATE TABLE IF NOT EXISTS identity.channel_bindings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL CHECK (channel_type IN ('wecom', 'feishu', 'telegram', 'dingtalk', 'webpush')),
    external_user_hash TEXT NOT NULL, -- 外部用户ID加盐哈希，防止明文关联
    external_username TEXT,
    binding_token_hash TEXT, -- 至少 128 bit 随机熵的一次性绑定码之带密钥摘要；不保存明文
    token_expires_at TIMESTAMPTZ,
    token_consumed_at TIMESTAMPTZ, -- 原子消费后写入，防止并发复用
    token_attempt_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('PENDING_VERIFY', 'ACTIVE', 'REVOKED')),
    bound_at TIMESTAMPTZ DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (channel_type, external_user_hash)
);

-- 6. 密钥引用表 (Secret References - 仅存 Vault/KMS 引用，绝不存明文密钥)
CREATE TABLE IF NOT EXISTS identity.secret_refs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES identity.tenants(id) ON DELETE CASCADE,
    secret_name TEXT NOT NULL,
    vault_uri TEXT NOT NULL,
    purpose TEXT NOT NULL,
    scope TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    rotated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
