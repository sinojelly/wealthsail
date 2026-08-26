-- =============================================================================
-- WealthSail Migration 017: 期初组合导入命令与角色授权收口
--
-- 背景
-- ----
-- Migration 008 已经定义了正确的 DRAFT -> CONFIRMED 导入模型，但 016 的
-- 临时 Web 命令绕过它，直接把用户输入写成 BUY/CASH_IN。本迁移只补齐既有
-- 模型所需的最小权限，并把账本写策略从“任意有效成员”收紧为 EDITOR 以上。
--
-- 经济语义
-- --------
-- * 草稿阶段不产生 ledger event；
-- * 用户确认后，每个导入项目产生一个带 import_batch_id/import_item_id 的
--   CONFIRMED ADJUSTMENT；
-- * POSITION 初始化只有 INSTRUMENT leg，不推断 BUY、SELL、CASH_IN/CASH_OUT；
-- * 已确认事实只能由后续 REVERSAL 冲正，不能 UPDATE/DELETE。
-- =============================================================================

GRANT SELECT, INSERT ON
    ledger.portfolio_import_batches,
    ledger.portfolio_import_items
TO wealthsail_api;

GRANT UPDATE (status, confirmed_at, cancelled_at, version, updated_at)
    ON ledger.portfolio_import_batches TO wealthsail_api;

DROP POLICY IF EXISTS domain_api_insert ON ledger.portfolio_import_batches;
CREATE POLICY domain_api_insert ON ledger.portfolio_import_batches
    FOR INSERT TO wealthsail_api
    WITH CHECK (
        identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR'])
        AND created_by = auth.uid()
    );

DROP POLICY IF EXISTS domain_api_update ON ledger.portfolio_import_batches;
CREATE POLICY domain_api_update ON ledger.portfolio_import_batches
    FOR UPDATE TO wealthsail_api
    USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']))
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));

DROP POLICY IF EXISTS domain_api_insert ON ledger.portfolio_import_items;
CREATE POLICY domain_api_insert ON ledger.portfolio_import_items
    FOR INSERT TO wealthsail_api
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));

-- Defense in depth: membership alone is not sufficient to create immutable
-- economic facts.  API guards provide the first check; RLS is the last check.
DROP POLICY IF EXISTS domain_api_insert ON ledger.ledger_events;
CREATE POLICY domain_api_insert ON ledger.ledger_events
    FOR INSERT TO wealthsail_api
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));

DROP POLICY IF EXISTS domain_api_insert ON ledger.ledger_legs;
CREATE POLICY domain_api_insert ON ledger.ledger_legs
    FOR INSERT TO wealthsail_api
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));

COMMENT ON TABLE ledger.portfolio_import_batches IS
    '期初组合导入命令聚合根；DRAFT 仅为待复核输入，CONFIRMED 后不可变。';
COMMENT ON TABLE ledger.portfolio_import_items IS
    '期初组合导入明细；确认时生成带来源关联的 ADJUSTMENT，不推断交易或现金流。';
