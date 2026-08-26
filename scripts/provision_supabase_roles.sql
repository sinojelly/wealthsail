-- ==============================================================================
-- WealthSail Supabase 角色 Provision（迁移 013 的“迁移外凭据”配置步骤）
-- ==============================================================================
-- 背景：迁移 013 创建了 NOLOGIN 的 wealthsail_api / wealthsail_worker 运行时角色，
--       但按设计“登录凭据在迁移之外提供”。后端代码在连接初始化时执行
--         API   : tenant_transaction 内  SET LOCAL ROLE wealthsail_api
--         Worker: create_pool init 时    SET ROLE wealthsail_worker（BYPASSRLS）
--       这要求连接用户必须是这两个角色的成员，且成员关系带 SET 选项
--       （PG16+ 的 WITH SET）。Supabase 平台授予 postgres 对自定义角色的
--       成员关系默认 SET 选项为 false，导致 Worker/API 启动时报：
--         asyncpg.exceptions.InsufficientPrivilegeError:
--           permission denied to set role "wealthsail_worker"
--
-- 用法：在 Supabase SQL Editor 以 postgres 执行（幂等，可重复执行）。
-- 若 API/Worker 使用独立登录角色（如 api-login / worker-login），
-- 请把下面 DO 块中的 'postgres' 替换为对应登录角色名。
-- 要求：PG 16+（GRANT ... WITH SET 语法）；当前 Supabase 为 PG 17。
-- ==============================================================================

-- 1. API 租户事务角色（tenant_transaction 的 SET LOCAL ROLE wealthsail_api）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles g ON g.oid = m.roleid
        JOIN pg_roles r ON r.oid = m.member
        WHERE g.rolname = 'wealthsail_api' AND r.rolname = 'postgres'
          AND m.set_option
    ) THEN
        EXECUTE 'GRANT wealthsail_api TO postgres WITH SET TRUE';
    END IF;
END $$;

-- 2. Worker 后台角色（create_pool init 的 SET ROLE wealthsail_worker，BYPASSRLS）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles g ON g.oid = m.roleid
        JOIN pg_roles r ON r.oid = m.member
        WHERE g.rolname = 'wealthsail_worker' AND r.rolname = 'postgres'
          AND m.set_option
    ) THEN
        EXECUTE 'GRANT wealthsail_worker TO postgres WITH SET TRUE';
    END IF;
END $$;

-- 3. 自检：以 postgres 身份应能 SET ROLE 到两个运行时角色
DO $$
DECLARE
    v_ok_api    boolean;
    v_ok_worker boolean;
BEGIN
    -- 检查成员关系 + SET 选项（不实际切换角色，避免影响当前会话）
    SELECT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles g ON g.oid = m.roleid
        JOIN pg_roles r ON r.oid = m.member
        WHERE g.rolname = 'wealthsail_api' AND r.rolname = 'postgres' AND m.set_option
    ) INTO v_ok_api;
    SELECT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles g ON g.oid = m.roleid
        JOIN pg_roles r ON r.oid = m.member
        WHERE g.rolname = 'wealthsail_worker' AND r.rolname = 'postgres' AND m.set_option
    ) INTO v_ok_worker;
    IF NOT v_ok_api OR NOT v_ok_worker THEN
        RAISE EXCEPTION 'PROVISION_FAILED: api_set=% worker_set=%', v_ok_api, v_ok_worker;
    END IF;
    RAISE NOTICE 'provision OK: SET ROLE wealthsail_api/wealthsail_worker now allowed for postgres';
END $$;
