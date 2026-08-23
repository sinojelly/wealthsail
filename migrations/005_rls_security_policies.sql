-- ==============================================================================
-- WealthSail Migration 005: Role-aware Row-Level Security Policies
-- Version: v2.0.1
-- Deployment profile: Supabase PostgreSQL (uses auth.uid())
-- Description:
--   1. RLS provides logical row isolation; it is not physical isolation.
--   2. Membership grants reads, not blanket writes.
--   3. Ledger, projections, workflow facts, and audit remain backend-controlled.
--   4. Table GRANTs and these policies must both allow an operation.
-- ==============================================================================

-- SECURITY DEFINER helpers use a fixed search_path and expose no dynamic SQL.
CREATE OR REPLACE FUNCTION identity.is_tenant_member(target_tenant UUID)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = identity, pg_catalog
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM identity.tenant_members tm
    WHERE tm.tenant_id = target_tenant
      AND tm.user_id = auth.uid()
      AND tm.status = 'ACTIVE'
  );
$$;

CREATE OR REPLACE FUNCTION identity.has_tenant_role(
    target_tenant UUID,
    allowed_roles TEXT[]
)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = identity, pg_catalog
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM identity.tenant_members tm
    WHERE tm.tenant_id = target_tenant
      AND tm.user_id = auth.uid()
      AND tm.status = 'ACTIVE'
      AND tm.role = ANY (allowed_roles)
  );
$$;

REVOKE ALL ON FUNCTION identity.is_tenant_member(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION identity.has_tenant_role(UUID, TEXT[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.is_tenant_member(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION identity.has_tenant_role(UUID, TEXT[]) TO authenticated;

-- All tenant-scoped tables are readable only by active members. This loop does
-- not create INSERT/UPDATE/DELETE policies; write access is defined separately.
DO $$
DECLARE
    table_name TEXT;
    tenant_tables TEXT[] := ARRAY[
        'identity.accounts',
        'identity.channel_bindings',
        'identity.secret_refs',
        'ledger.ledger_events',
        'ledger.ledger_legs',
        'ledger.trade_fills',
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
    FOREACH table_name IN ARRAY tenant_tables LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_member_select ON %s', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_member_select ON %s FOR SELECT TO authenticated USING (identity.is_tenant_member(tenant_id))',
            table_name
        );
    END LOOP;
END $$;

-- Direct user writes are limited to mutable configuration/intent resources.
-- Ledger, projections, reports, model runs, Outbox, and audit have no direct
-- authenticated write policy; trusted application commands own those writes.
DO $$
DECLARE
    table_name TEXT;
    editor_tables TEXT[] := ARRAY[
        'identity.accounts',
        'research.theses',
        'risk.investment_policies',
        'risk.alert_rules'
    ];
BEGIN
    FOREACH table_name IN ARRAY editor_tables LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_editor_insert ON %s', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_editor_update ON %s', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_editor_delete ON %s', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_editor_insert ON %s FOR INSERT TO authenticated WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY[''OWNER'',''ADMIN'',''EDITOR'']))',
            table_name
        );
        EXECUTE format(
            'CREATE POLICY tenant_editor_update ON %s FOR UPDATE TO authenticated USING (identity.has_tenant_role(tenant_id, ARRAY[''OWNER'',''ADMIN'',''EDITOR''])) WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY[''OWNER'',''ADMIN'',''EDITOR'']))',
            table_name
        );
        EXECUTE format(
            'CREATE POLICY tenant_editor_delete ON %s FOR DELETE TO authenticated USING (identity.has_tenant_role(tenant_id, ARRAY[''OWNER'',''ADMIN'']))',
            table_name
        );
    END LOOP;
END $$;

-- A channel binding is self-owned. Cross-user binding and administrative
-- recovery go through the trusted Domain API and are audited.
DROP POLICY IF EXISTS channel_binding_self_insert ON identity.channel_bindings;
CREATE POLICY channel_binding_self_insert
ON identity.channel_bindings
FOR INSERT TO authenticated
WITH CHECK (
    user_id = auth.uid()
    AND identity.is_tenant_member(tenant_id)
);

DROP POLICY IF EXISTS channel_binding_self_update ON identity.channel_bindings;
CREATE POLICY channel_binding_self_update
ON identity.channel_bindings
FOR UPDATE TO authenticated
USING (user_id = auth.uid() AND identity.is_tenant_member(tenant_id))
WITH CHECK (user_id = auth.uid() AND identity.is_tenant_member(tenant_id));

-- Non-tenant identity tables need explicit policies instead of the generic
-- tenant_id template.
ALTER TABLE identity.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.tenants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_self_select ON identity.tenants;
CREATE POLICY tenant_self_select
ON identity.tenants
FOR SELECT TO authenticated
USING (identity.is_tenant_member(id));

ALTER TABLE identity.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS profile_self_select ON identity.profiles;
CREATE POLICY profile_self_select
ON identity.profiles
FOR SELECT TO authenticated
USING (id = auth.uid());
DROP POLICY IF EXISTS profile_self_update ON identity.profiles;
CREATE POLICY profile_self_update
ON identity.profiles
FOR UPDATE TO authenticated
USING (id = auth.uid())
WITH CHECK (id = auth.uid());

ALTER TABLE identity.tenant_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.tenant_members FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_members_self_select ON identity.tenant_members;
CREATE POLICY tenant_members_self_select
ON identity.tenant_members
FOR SELECT TO authenticated
USING (user_id = auth.uid() OR identity.is_tenant_member(tenant_id));

COMMENT ON FUNCTION identity.is_tenant_member(UUID) IS
    'Supabase membership predicate for logical row isolation; not an authorization substitute for Domain API commands.';

-- RLS policies do not grant table privileges by themselves. These explicit
-- grants keep browser/JWT access read-mostly; financial facts and workflow
-- tables remain writable only by the separately controlled Domain API role.
GRANT USAGE ON SCHEMA identity, ledger, research, reporting, risk, workflow, audit TO authenticated;
GRANT SELECT ON
    identity.tenants, identity.profiles, identity.tenant_members,
    identity.accounts, identity.channel_bindings, identity.secret_refs,
    ledger.ledger_events, ledger.ledger_legs, ledger.trade_fills,
    ledger.position_lots, ledger.position_snapshots, ledger.account_nav_snapshots,
    research.theses, research.model_runs, research.recommendations, research.evidence_refs,
    reporting.reports, reporting.report_items, reporting.decisions,
    risk.investment_policies, risk.alert_rules, risk.alert_events,
    workflow.jobs, workflow.job_runs, workflow.outbox, audit.audit_log
TO authenticated;

GRANT INSERT, UPDATE, DELETE ON
    identity.accounts, research.theses, risk.investment_policies, risk.alert_rules
TO authenticated;
GRANT INSERT, UPDATE ON identity.channel_bindings TO authenticated;
GRANT UPDATE ON identity.profiles TO authenticated;
