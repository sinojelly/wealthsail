-- ==============================================================================
-- WealthSail Migration 016: 用户手动添加持仓 - Domain API 写权限
--
-- 场景：Web 端"添加持仓"由 Domain API（wealthsail_api，tenant RLS 事务）执行：
--   1. 写入不可变账本 ledger.ledger_events / ledger.ledger_legs（BUY 事件）
--   2. 可能创建 reference.instruments / instrument_aliases（全局主数据，无 RLS）
--   3. 可能创建 identity.accounts（租户账户，005 editor policies 已覆盖
--      authenticated 角色，wealthsail_api 继承该成员关系）
--
-- 缺口（本次补齐，均幂等可重跑）：
--   - ledger.ledger_events / ledger_legs：FORCE RLS（005）但无任何 INSERT
--     policy（009 只覆盖投影/报告/outbox 等表），且无 INSERT 表权限
--   - reference.instruments / instrument_aliases：无 RLS，但 wealthsail_api
--     从未获得表级 SELECT/INSERT（仅通过继承 authenticated 拿到 schema USAGE）
--   - identity.accounts：013 已 GRANT INSERT/UPDATE/DELETE；005 editor
--     policies（TO authenticated，API 角色成员继承）已覆盖行级校验，无需新增
-- ==============================================================================

GRANT USAGE ON SCHEMA reference, ledger TO wealthsail_api;

-- 标的主数据（全局表，无 RLS：只有表权限，无行级策略）
GRANT SELECT, INSERT ON reference.instruments TO wealthsail_api;
GRANT SELECT, INSERT ON reference.instrument_aliases TO wealthsail_api;

-- 不可变账本（FORCE RLS：租户作用域 INSERT 策略 + 表权限）
GRANT SELECT, INSERT ON ledger.ledger_events TO wealthsail_api;
GRANT SELECT, INSERT ON ledger.ledger_legs TO wealthsail_api;

DROP POLICY IF EXISTS domain_api_insert ON ledger.ledger_events;
CREATE POLICY domain_api_insert ON ledger.ledger_events
    FOR INSERT TO wealthsail_api
    WITH CHECK (identity.is_tenant_member(tenant_id));

DROP POLICY IF EXISTS domain_api_insert ON ledger.ledger_legs;
CREATE POLICY domain_api_insert ON ledger.ledger_legs
    FOR INSERT TO wealthsail_api
    WITH CHECK (identity.is_tenant_member(tenant_id));
