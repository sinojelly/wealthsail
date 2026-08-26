-- ==============================================================================
-- WealthSail Migration 001: Initial Schemas & Extensions
-- Version: v2.0.0
-- Description: 创建领域驱动的 PostgreSQL Schemas 与通用扩展
-- ==============================================================================

-- 1. 启用必要扩展 (UUID与密码学哈希)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Supabase provides the authenticated role and auth.uid(). A plain PostgreSQL
-- deployment needs the same narrow compatibility surface because the Domain
-- API injects verified JWT claims into request.jwt.claims before RLS queries.
-- Never create LOGIN credentials here; deployment login roles are provisioned
-- outside schema migrations.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN INHERIT NOBYPASSRLS;
    END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS auth;
DO $$
BEGIN
    IF to_regprocedure('auth.uid()') IS NULL THEN
        EXECUTE $function$
            CREATE FUNCTION auth.uid()
            RETURNS UUID
            LANGUAGE sql
            STABLE
            SET search_path = pg_catalog
            AS $body$
              SELECT (
                NULLIF(current_setting('request.jwt.claims', true), '')::jsonb
                ->> 'sub'
              )::uuid
            $body$
        $function$;
    END IF;
END $$;
GRANT USAGE ON SCHEMA auth TO authenticated;
GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated;

-- 2. 创建各业务领域隔离 Schema
CREATE SCHEMA IF NOT EXISTS identity;   -- 租户、用户、账户与身份绑定
CREATE SCHEMA IF NOT EXISTS reference;  -- 标的主数据、交易日历与数据源元数据
CREATE SCHEMA IF NOT EXISTS ledger;     -- 不可变投资事件、成交事实、批次与持仓/净值投影
CREATE SCHEMA IF NOT EXISTS market;     -- 数据源抓取快照、市场指标观察与公司行动
CREATE SCHEMA IF NOT EXISTS research;   -- 投资论点、模型执行记录、建议与证据引用
CREATE SCHEMA IF NOT EXISTS reporting;  -- 四时段报告、条目、用户确认/纠正决策
CREATE SCHEMA IF NOT EXISTS risk;       -- 账户投资政策、风控告警规则与触点事件
CREATE SCHEMA IF NOT EXISTS workflow;   -- 定时任务调度、JobRuns 与事务性 Outbox
CREATE SCHEMA IF NOT EXISTS audit;      -- 统一不可变审计日志
