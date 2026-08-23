-- ==============================================================================
-- WealthSail Migration 005: Row-Level Security (RLS) Policies
-- Version: v2.0.0
-- Description: 强制启用多租户行级安全物理隔离策略
-- ==============================================================================

-- 1. 租户成员安全断言函数 (Security Definer)
CREATE OR REPLACE FUNCTION identity.is_tenant_member(target_tenant UUID)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = identity, public
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM identity.tenant_members tm
    WHERE tm.tenant_id = target_tenant
      AND tm.user_id = auth.uid()
      AND tm.status = 'ACTIVE'
  );
$$;

-- 2. 批量启用并强制 RLS
DO $$
DECLARE
    t text;
    tables text[] := ARRAY[
        'identity.accounts',
        'identity.channel_bindings',
        'identity.secret_refs',
        'ledger.ledger_events',
        'ledger.ledger_legs',
        'ledger.position_lots',
        'ledger.position_snapshots',
        'ledger.account_nav_snapshots',
        'research.theses',
        'research.model_runs',
        'research.recommendations',
        'research.evidence_refs',
        'reporting.reports',
        'reporting.report_items',
        'reporting.decisions',
        'risk.investment_policies',
        'risk.alert_rules',
        'risk.alert_events',
        'workflow.jobs',
        'workflow.job_runs',
        'workflow.outbox',
        'audit.audit_log'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY;', t);
        
        -- 创建 SELECT 策略
        EXECUTE format('DROP POLICY IF EXISTS p_select_tenant ON %s;', t);
        EXECUTE format('CREATE POLICY p_select_tenant ON %s FOR SELECT USING (identity.is_tenant_member(tenant_id));', t);
        
        -- 创建 INSERT 策略
        EXECUTE format('DROP POLICY IF EXISTS p_insert_tenant ON %s;', t);
        EXECUTE format('CREATE POLICY p_insert_tenant ON %s FOR INSERT WITH CHECK (identity.is_tenant_member(tenant_id));', t);
        
        -- 创建 UPDATE 策略
        EXECUTE format('DROP POLICY IF EXISTS p_update_tenant ON %s;', t);
        EXECUTE format('CREATE POLICY p_update_tenant ON %s FOR UPDATE USING (identity.is_tenant_member(tenant_id));', t);
        
        -- 创建 DELETE 策略
        EXECUTE format('DROP POLICY IF EXISTS p_delete_tenant ON %s;', t);
        EXECUTE format('CREATE POLICY p_delete_tenant ON %s FOR DELETE USING (identity.is_tenant_member(tenant_id));', t);
    END LOOP;
END $$;
