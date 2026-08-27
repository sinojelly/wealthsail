-- =============================================================================
-- WealthSail Migration 021: 成交与决策的服务层写权限
--
-- modules/ledger/repository.py（record_trade / create_trade_fill_draft /
-- confirm_trade_fill / list_trade_fills）与 modules/research/service.py
-- （record_decision / retract_decision / list_decisions）落地后，补齐
-- ledger.trade_fills 与 reporting.decisions 的表级权限和 RLS 写策略。
--
-- 007 只对两表 FORCE RLS 并建立 SELECT 策略（tenant_member_select TO
-- authenticated，wealthsail_api 继承该角色）；写路径此前无任何授权，
-- 直接执行会被 FORCE RLS + 无表权限双重拒绝。本迁移对齐 016/017 的
-- domain_api_* 策略风格（写操作限 OWNER/ADMIN/EDITOR）。
-- 幂等可重跑。
-- =============================================================================

-- 0. Schema 访问（幂等；不依赖 016/009 的执行顺序）
GRANT USAGE ON SCHEMA ledger, reporting TO wealthsail_api;

-- 1. 表级权限（最小授权：仅业务需要的 DML 与列）
GRANT SELECT, INSERT ON ledger.trade_fills TO wealthsail_api;
GRANT SELECT, INSERT ON reporting.decisions TO wealthsail_api;

-- confirm_trade_fill：DRAFT → CONFIRMED 回填 ledger_event_id/confirmed_at
GRANT UPDATE (status, ledger_event_id, confirmed_at)
    ON ledger.trade_fills TO wealthsail_api;

-- retract_decision：RECORDED → RETRACTED 软撤（status/reason/correction 可修正）
GRANT UPDATE (status, reason, correction)
    ON reporting.decisions TO wealthsail_api;

-- 2. RLS 写策略（租户成员 + 编辑角色校验由 identity.has_tenant_role 完成）
DROP POLICY IF EXISTS domain_api_insert ON ledger.trade_fills;
CREATE POLICY domain_api_insert ON ledger.trade_fills
    FOR INSERT TO wealthsail_api
    WITH CHECK (
        identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR'])
    );

DROP POLICY IF EXISTS domain_api_update ON ledger.trade_fills;
CREATE POLICY domain_api_update ON ledger.trade_fills
    FOR UPDATE TO wealthsail_api
    USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']))
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));

DROP POLICY IF EXISTS domain_api_insert ON reporting.decisions;
CREATE POLICY domain_api_insert ON reporting.decisions
    FOR INSERT TO wealthsail_api
    WITH CHECK (
        identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR'])
        AND user_id = auth.uid()
    );

DROP POLICY IF EXISTS domain_api_update ON reporting.decisions;
CREATE POLICY domain_api_update ON reporting.decisions
    FOR UPDATE TO wealthsail_api
    USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']))
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));

COMMENT ON POLICY domain_api_insert ON ledger.trade_fills IS
    '成交记录仅限编辑角色成员写入（modules/ledger/repository.py）';
COMMENT ON POLICY domain_api_update ON ledger.trade_fills IS
    '草稿确认回填 ledger_event_id 的状态迁移（confirm_trade_fill）';
COMMENT ON POLICY domain_api_insert ON reporting.decisions IS
    '决策必须由本人且为编辑角色成员记录（modules/research/service.py）';
COMMENT ON POLICY domain_api_update ON reporting.decisions IS
    '撤回/修正决策元数据（retract_decision）';
