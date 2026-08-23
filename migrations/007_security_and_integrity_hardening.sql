-- ==============================================================================
-- WealthSail Migration 007: Security and Tenant Integrity Hardening
-- Version: v2.0.1
-- Purpose: upgrade installations that already applied migrations 001-006.
-- This migration is additive and preserves stable IDs.
-- ==============================================================================

-- Fail before adding composite foreign keys if legacy data already contains a
-- cross-tenant relationship. Operators must investigate instead of silently
-- rewriting an economic fact.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM ledger.ledger_events event
        JOIN identity.accounts account ON account.id = event.account_id
        WHERE event.tenant_id <> account.tenant_id
    ) THEN
        RAISE EXCEPTION 'cross-tenant ledger event/account relationship detected';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM ledger.ledger_legs leg
        JOIN ledger.ledger_events event ON event.id = leg.event_id
        WHERE leg.tenant_id <> event.tenant_id
    ) THEN
        RAISE EXCEPTION 'cross-tenant ledger leg/event relationship detected';
    END IF;
END $$;

-- Replace the original blanket CRUD policies on already-migrated databases.
-- The helper definitions are repeated here because an installation that has
-- already recorded migration 005 will not execute the revised 005 file again.
CREATE OR REPLACE FUNCTION identity.is_tenant_member(target_tenant UUID)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = identity, pg_catalog
AS $$
  SELECT EXISTS (
    SELECT 1 FROM identity.tenant_members tm
    WHERE tm.tenant_id = target_tenant
      AND tm.user_id = auth.uid()
      AND tm.status = 'ACTIVE'
  );
$$;

CREATE OR REPLACE FUNCTION identity.has_tenant_role(target_tenant UUID, allowed_roles TEXT[])
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = identity, pg_catalog
AS $$
  SELECT EXISTS (
    SELECT 1 FROM identity.tenant_members tm
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

-- Separate an investment decision from a real fill. Older installations used
-- CONFIRMED/CORRECTED/NOT_EXECUTED as an overloaded decision status.
ALTER TABLE reporting.decisions
    ADD COLUMN IF NOT EXISTS decision_action TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'reporting' AND table_name = 'decisions'
          AND column_name = 'confirmed_at'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'reporting' AND table_name = 'decisions'
          AND column_name = 'decided_at'
    ) THEN
        ALTER TABLE reporting.decisions RENAME COLUMN confirmed_at TO decided_at;
    END IF;
END $$;

UPDATE reporting.decisions
SET decision_action = CASE status
    WHEN 'CONFIRMED' THEN 'ACCEPTED'
    WHEN 'NOT_EXECUTED' THEN 'IGNORED'
    WHEN 'CORRECTED' THEN 'DEFERRED'
    ELSE COALESCE(decision_action, 'DEFERRED')
END
WHERE decision_action IS NULL;

ALTER TABLE reporting.decisions DROP CONSTRAINT IF EXISTS decisions_status_check;
ALTER TABLE reporting.decisions DROP CONSTRAINT IF EXISTS decisions_decision_action_check;
UPDATE reporting.decisions
SET status = CASE status
    WHEN 'CONFIRMED' THEN 'RECORDED'
    WHEN 'NOT_EXECUTED' THEN 'RECORDED'
    WHEN 'CORRECTED' THEN 'SUPERSEDED'
    ELSE status
END;
ALTER TABLE reporting.decisions
    ALTER COLUMN decision_action SET NOT NULL,
    ALTER COLUMN status SET DEFAULT 'RECORDED',
    ADD CONSTRAINT decisions_decision_action_check
        CHECK (decision_action IN ('ACCEPTED', 'IGNORED', 'DEFERRED')),
    ADD CONSTRAINT decisions_status_check
        CHECK (status IN ('RECORDED', 'RETRACTED', 'SUPERSEDED'));

CREATE TABLE IF NOT EXISTS ledger.trade_fills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES identity.accounts(id) ON DELETE RESTRICT,
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id),
    decision_id UUID REFERENCES reporting.decisions(id) ON DELETE SET NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity NUMERIC(28, 10) NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(28, 10) NOT NULL CHECK (unit_price > 0),
    fee NUMERIC(28, 8) NOT NULL DEFAULT 0 CHECK (fee >= 0),
    trade_date DATE NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('USER_CONFIRMED', 'STATEMENT_IMPORT', 'BROKER_FILL')),
    external_fill_id TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'CONFIRMED', 'REJECTED', 'CANCELLED')),
    ledger_event_id UUID REFERENCES ledger.ledger_events(id) ON DELETE RESTRICT,
    created_by UUID NOT NULL REFERENCES identity.profiles(id),
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    CHECK (
        (status = 'CONFIRMED' AND ledger_event_id IS NOT NULL AND confirmed_at IS NOT NULL)
        OR (status <> 'CONFIRMED' AND ledger_event_id IS NULL AND confirmed_at IS NULL)
    ),
    UNIQUE (tenant_id, idempotency_key)
);

-- Pending HITL state is not a decision. Store a one-time, tenant-bound action
-- request and append reporting.decisions only after the user actually acts.
CREATE TABLE IF NOT EXISTS workflow.interaction_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE RESTRICT,
    channel_binding_id UUID NOT NULL REFERENCES identity.channel_bindings(id) ON DELETE RESTRICT,
    report_id UUID REFERENCES reporting.reports(id) ON DELETE RESTRICT,
    recommendation_id UUID REFERENCES research.recommendations(id) ON DELETE RESTRICT,
    request_type TEXT NOT NULL CHECK (request_type IN ('DECISION_PROMPT', 'FILL_CONFIRMATION', 'PORTFOLIO_CONFIRMATION')),
    allowed_actions TEXT[] NOT NULL CHECK (
        cardinality(allowed_actions) > 0
        AND array_position(allowed_actions, NULL) IS NULL
    ),
    action_token_hash TEXT NOT NULL,
    token_expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CONSUMED', 'EXPIRED', 'CANCELLED')),
    consumed_action TEXT,
    resolved_at TIMESTAMPTZ,
    payload_schema_version INTEGER NOT NULL DEFAULT 1 CHECK (payload_schema_version > 0),
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (token_expires_at > created_at),
    CHECK (report_id IS NOT NULL OR recommendation_id IS NOT NULL),
    CHECK (consumed_action IS NULL OR consumed_action = ANY (allowed_actions)),
    CHECK (
        (status = 'OPEN' AND consumed_action IS NULL AND resolved_at IS NULL)
        OR (status = 'CONSUMED' AND consumed_action IS NOT NULL AND resolved_at IS NOT NULL)
        OR (status IN ('EXPIRED', 'CANCELLED') AND consumed_action IS NULL AND resolved_at IS NOT NULL)
    ),
    UNIQUE (tenant_id, idempotency_key),
    UNIQUE (tenant_id, action_token_hash)
);

CREATE INDEX IF NOT EXISTS idx_interaction_requests_open_expiry
ON workflow.interaction_requests (tenant_id, token_expires_at)
WHERE status = 'OPEN';

DO $$
DECLARE
    table_name TEXT;
    tenant_tables TEXT[] := ARRAY[
        'identity.accounts', 'identity.channel_bindings', 'identity.secret_refs',
        'ledger.ledger_events', 'ledger.ledger_legs', 'ledger.trade_fills', 'ledger.position_lots',
        'ledger.position_snapshots', 'ledger.account_nav_snapshots',
        'research.theses', 'research.model_runs', 'research.recommendations',
        'research.evidence_refs', 'reporting.reports', 'reporting.report_items',
        'reporting.decisions', 'risk.investment_policies', 'risk.alert_rules',
        'risk.alert_events', 'workflow.jobs', 'workflow.job_runs',
        'workflow.interaction_requests',
        'workflow.outbox', 'audit.audit_log'
    ];
BEGIN
    FOREACH table_name IN ARRAY tenant_tables LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS p_select_tenant ON %s', table_name);
        EXECUTE format('DROP POLICY IF EXISTS p_insert_tenant ON %s', table_name);
        EXECUTE format('DROP POLICY IF EXISTS p_update_tenant ON %s', table_name);
        EXECUTE format('DROP POLICY IF EXISTS p_delete_tenant ON %s', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_member_select ON %s', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_member_select ON %s FOR SELECT TO authenticated USING (identity.is_tenant_member(tenant_id))',
            table_name
        );
    END LOOP;
END $$;

DROP POLICY IF EXISTS tenant_member_select ON workflow.interaction_requests;
DROP POLICY IF EXISTS interaction_request_self_select ON workflow.interaction_requests;
CREATE POLICY interaction_request_self_select
ON workflow.interaction_requests FOR SELECT TO authenticated
USING (user_id = auth.uid() AND identity.is_tenant_member(tenant_id));

DO $$
DECLARE
    table_name TEXT;
    editor_tables TEXT[] := ARRAY[
        'identity.accounts', 'research.theses',
        'risk.investment_policies', 'risk.alert_rules'
    ];
BEGIN
    FOREACH table_name IN ARRAY editor_tables LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_editor_insert ON %s', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_editor_update ON %s', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_editor_delete ON %s', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_editor_insert ON %s FOR INSERT TO authenticated WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY[''OWNER'',''ADMIN'',''EDITOR'']))', table_name
        );
        EXECUTE format(
            'CREATE POLICY tenant_editor_update ON %s FOR UPDATE TO authenticated USING (identity.has_tenant_role(tenant_id, ARRAY[''OWNER'',''ADMIN'',''EDITOR''])) WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY[''OWNER'',''ADMIN'',''EDITOR'']))', table_name
        );
        EXECUTE format(
            'CREATE POLICY tenant_editor_delete ON %s FOR DELETE TO authenticated USING (identity.has_tenant_role(tenant_id, ARRAY[''OWNER'',''ADMIN'']))', table_name
        );
    END LOOP;
END $$;

DROP POLICY IF EXISTS channel_binding_self_insert ON identity.channel_bindings;
CREATE POLICY channel_binding_self_insert
ON identity.channel_bindings FOR INSERT TO authenticated
WITH CHECK (user_id = auth.uid() AND identity.is_tenant_member(tenant_id));
DROP POLICY IF EXISTS channel_binding_self_update ON identity.channel_bindings;
CREATE POLICY channel_binding_self_update
ON identity.channel_bindings FOR UPDATE TO authenticated
USING (user_id = auth.uid() AND identity.is_tenant_member(tenant_id))
WITH CHECK (user_id = auth.uid() AND identity.is_tenant_member(tenant_id));

ALTER TABLE identity.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.tenants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_self_select ON identity.tenants;
CREATE POLICY tenant_self_select ON identity.tenants FOR SELECT TO authenticated
USING (identity.is_tenant_member(id));
ALTER TABLE identity.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS profile_self_select ON identity.profiles;
CREATE POLICY profile_self_select ON identity.profiles FOR SELECT TO authenticated USING (id = auth.uid());
DROP POLICY IF EXISTS profile_self_update ON identity.profiles;
CREATE POLICY profile_self_update ON identity.profiles FOR UPDATE TO authenticated
USING (id = auth.uid()) WITH CHECK (id = auth.uid());
ALTER TABLE identity.tenant_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.tenant_members FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_members_self_select ON identity.tenant_members;
CREATE POLICY tenant_members_self_select ON identity.tenant_members FOR SELECT TO authenticated
USING (user_id = auth.uid() OR identity.is_tenant_member(tenant_id));

GRANT USAGE ON SCHEMA identity, ledger, research, reporting, risk, workflow, audit TO authenticated;
GRANT SELECT ON
    identity.tenants, identity.profiles, identity.tenant_members,
    identity.accounts, identity.channel_bindings, identity.secret_refs,
    ledger.ledger_events, ledger.ledger_legs, ledger.trade_fills,
    ledger.position_lots, ledger.position_snapshots, ledger.account_nav_snapshots,
    research.theses, research.model_runs, research.recommendations, research.evidence_refs,
    reporting.reports, reporting.report_items, reporting.decisions,
    risk.investment_policies, risk.alert_rules, risk.alert_events,
    workflow.jobs, workflow.job_runs, workflow.interaction_requests,
    workflow.outbox, audit.audit_log
TO authenticated;
REVOKE SELECT ON workflow.interaction_requests FROM authenticated;
GRANT SELECT (
    id, tenant_id, user_id, channel_binding_id, report_id, recommendation_id,
    request_type, allowed_actions, token_expires_at, status, consumed_action,
    resolved_at, payload_schema_version, created_at
) ON workflow.interaction_requests TO authenticated;
GRANT INSERT, UPDATE, DELETE ON
    identity.accounts, research.theses, risk.investment_policies, risk.alert_rules
TO authenticated;
GRANT INSERT, UPDATE ON identity.channel_bindings TO authenticated;
GRANT UPDATE ON identity.profiles TO authenticated;

-- Link research/report artifacts to the durable job execution that produced
-- them. Older 004 schemas did not yet contain these additive columns.
ALTER TABLE research.recommendations
    ADD COLUMN IF NOT EXISTS job_run_id UUID;

-- Stable same-tenant parent keys.
ALTER TABLE ledger.ledger_events
    ADD CONSTRAINT uq_ledger_events_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE research.theses
    ADD CONSTRAINT uq_theses_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE research.model_runs
    ADD CONSTRAINT uq_model_runs_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE research.recommendations
    ADD CONSTRAINT uq_recommendations_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE reporting.reports
    ADD CONSTRAINT uq_reports_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE reporting.decisions
    ADD CONSTRAINT uq_decisions_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE identity.channel_bindings
    ADD CONSTRAINT uq_channel_bindings_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE ledger.trade_fills
    ADD CONSTRAINT uq_trade_fills_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE risk.alert_rules
    ADD CONSTRAINT uq_alert_rules_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE workflow.jobs
    ADD CONSTRAINT uq_jobs_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE workflow.job_runs
    ADD CONSTRAINT uq_job_runs_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE workflow.outbox
    ADD CONSTRAINT uq_outbox_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE workflow.interaction_requests
    ADD CONSTRAINT uq_interaction_requests_tenant_id UNIQUE (tenant_id, id);

-- Composite foreign keys prevent a valid tenant row from referencing another
-- tenant's valid account/event/report. Existing single-column FKs remain as a
-- compatibility belt-and-suspenders constraint.
ALTER TABLE ledger.ledger_events
    ADD CONSTRAINT fk_ledger_events_tenant_account
    FOREIGN KEY (tenant_id, account_id)
    REFERENCES identity.accounts(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE ledger.ledger_events
    ADD CONSTRAINT fk_ledger_events_tenant_reversal
    FOREIGN KEY (tenant_id, reverses_event_id)
    REFERENCES ledger.ledger_events(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE ledger.ledger_legs
    ADD CONSTRAINT fk_ledger_legs_tenant_event
    FOREIGN KEY (tenant_id, event_id)
    REFERENCES ledger.ledger_events(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE ledger.position_lots
    ADD CONSTRAINT fk_position_lots_tenant_account
    FOREIGN KEY (tenant_id, account_id)
    REFERENCES identity.accounts(tenant_id, id) ON DELETE CASCADE;
ALTER TABLE ledger.position_snapshots
    ADD CONSTRAINT fk_position_snapshots_tenant_account
    FOREIGN KEY (tenant_id, account_id)
    REFERENCES identity.accounts(tenant_id, id) ON DELETE CASCADE;
ALTER TABLE ledger.account_nav_snapshots
    ADD CONSTRAINT fk_nav_snapshots_tenant_account
    FOREIGN KEY (tenant_id, account_id)
    REFERENCES identity.accounts(tenant_id, id) ON DELETE CASCADE;
ALTER TABLE identity.channel_bindings
    ADD CONSTRAINT fk_channel_bindings_tenant_user
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT;
ALTER TABLE research.theses
    ADD CONSTRAINT fk_theses_tenant_account
    FOREIGN KEY (tenant_id, account_id)
    REFERENCES identity.accounts(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE research.recommendations
    ADD CONSTRAINT fk_recommendations_tenant_thesis
    FOREIGN KEY (tenant_id, thesis_id)
    REFERENCES research.theses(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE research.recommendations
    ADD CONSTRAINT fk_recommendations_tenant_model_run
    FOREIGN KEY (tenant_id, model_run_id)
    REFERENCES research.model_runs(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE research.recommendations
    ADD CONSTRAINT fk_recommendations_tenant_job_run
    FOREIGN KEY (tenant_id, job_run_id)
    REFERENCES workflow.job_runs(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE research.evidence_refs
    ADD CONSTRAINT fk_evidence_tenant_recommendation
    FOREIGN KEY (tenant_id, recommendation_id)
    REFERENCES research.recommendations(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE reporting.reports
    ADD CONSTRAINT fk_reports_tenant_model_run
    FOREIGN KEY (tenant_id, model_run_id)
    REFERENCES research.model_runs(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE reporting.reports
    ADD CONSTRAINT fk_reports_tenant_superseded
    FOREIGN KEY (tenant_id, supersedes_report_id)
    REFERENCES reporting.reports(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE reporting.report_items
    ADD CONSTRAINT fk_report_items_tenant_report
    FOREIGN KEY (tenant_id, report_id)
    REFERENCES reporting.reports(tenant_id, id) ON DELETE CASCADE;
ALTER TABLE reporting.decisions
    ADD CONSTRAINT fk_decisions_tenant_user
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT;
ALTER TABLE reporting.decisions
    ADD CONSTRAINT fk_decisions_tenant_report
    FOREIGN KEY (tenant_id, report_id)
    REFERENCES reporting.reports(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE reporting.decisions
    ADD CONSTRAINT fk_decisions_tenant_recommendation
    FOREIGN KEY (tenant_id, recommendation_id)
    REFERENCES research.recommendations(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE reporting.decisions
    ADD CONSTRAINT fk_decisions_tenant_previous
    FOREIGN KEY (tenant_id, previous_decision_id)
    REFERENCES reporting.decisions(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE workflow.interaction_requests
    ADD CONSTRAINT fk_interaction_requests_tenant_user
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT;
ALTER TABLE workflow.interaction_requests
    ADD CONSTRAINT fk_interaction_requests_tenant_binding
    FOREIGN KEY (tenant_id, channel_binding_id)
    REFERENCES identity.channel_bindings(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE workflow.interaction_requests
    ADD CONSTRAINT fk_interaction_requests_tenant_report
    FOREIGN KEY (tenant_id, report_id)
    REFERENCES reporting.reports(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE workflow.interaction_requests
    ADD CONSTRAINT fk_interaction_requests_tenant_recommendation
    FOREIGN KEY (tenant_id, recommendation_id)
    REFERENCES research.recommendations(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE ledger.trade_fills
    ADD CONSTRAINT fk_trade_fills_tenant_account
    FOREIGN KEY (tenant_id, account_id)
    REFERENCES identity.accounts(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE ledger.trade_fills
    ADD CONSTRAINT fk_trade_fills_tenant_decision
    FOREIGN KEY (tenant_id, decision_id)
    REFERENCES reporting.decisions(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE ledger.trade_fills
    ADD CONSTRAINT fk_trade_fills_tenant_event
    FOREIGN KEY (tenant_id, ledger_event_id)
    REFERENCES ledger.ledger_events(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE ledger.trade_fills
    ADD CONSTRAINT fk_trade_fills_tenant_user
    FOREIGN KEY (tenant_id, created_by)
    REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT;
ALTER TABLE risk.alert_events
    ADD CONSTRAINT fk_alert_events_tenant_rule
    FOREIGN KEY (tenant_id, rule_id)
    REFERENCES risk.alert_rules(tenant_id, id) ON DELETE CASCADE;
ALTER TABLE risk.investment_policies
    ADD CONSTRAINT fk_policies_tenant_account
    FOREIGN KEY (tenant_id, account_id)
    REFERENCES identity.accounts(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE risk.alert_rules
    ADD CONSTRAINT fk_alert_rules_tenant_account
    FOREIGN KEY (tenant_id, account_id)
    REFERENCES identity.accounts(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE risk.alert_rules
    ADD CONSTRAINT fk_alert_rules_tenant_thesis
    FOREIGN KEY (tenant_id, thesis_id)
    REFERENCES research.theses(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE workflow.job_runs
    ADD CONSTRAINT fk_job_runs_tenant_job
    FOREIGN KEY (tenant_id, job_id)
    REFERENCES workflow.jobs(tenant_id, id) ON DELETE CASCADE;
ALTER TABLE risk.alert_events
    ADD CONSTRAINT fk_alert_events_tenant_outbox
    FOREIGN KEY (tenant_id, outbox_id)
    REFERENCES workflow.outbox(tenant_id, id) ON DELETE RESTRICT;

-- Legacy percentage defaults were examples, not universally suitable user
-- policies. Preserve existing values for review, but mark them DRAFT and stop
-- silently creating new financial constraints or L4 asset targets.
ALTER TABLE risk.investment_policies
    ADD COLUMN IF NOT EXISTS policy_status TEXT NOT NULL DEFAULT 'DRAFT';
ALTER TABLE risk.investment_policies
    ALTER COLUMN max_drawdown_limit DROP DEFAULT,
    ALTER COLUMN single_position_limit DROP DEFAULT,
    ALTER COLUMN equity_target_pct DROP DEFAULT,
    ALTER COLUMN bond_target_pct DROP DEFAULT,
    ALTER COLUMN gold_target_pct DROP DEFAULT,
    ALTER COLUMN cash_target_pct DROP DEFAULT,
    ALTER COLUMN rebalance_tolerance_pct DROP DEFAULT;
ALTER TABLE risk.investment_policies
    DROP CONSTRAINT IF EXISTS investment_policies_status_check,
    DROP CONSTRAINT IF EXISTS investment_policies_drawdown_check,
    DROP CONSTRAINT IF EXISTS investment_policies_single_position_check,
    DROP CONSTRAINT IF EXISTS investment_policies_equity_target_check,
    DROP CONSTRAINT IF EXISTS investment_policies_bond_target_check,
    DROP CONSTRAINT IF EXISTS investment_policies_gold_target_check,
    DROP CONSTRAINT IF EXISTS investment_policies_cash_target_check,
    DROP CONSTRAINT IF EXISTS investment_policies_rebalance_check,
    DROP CONSTRAINT IF EXISTS investment_policies_effective_dates_check;
ALTER TABLE risk.investment_policies
    ADD CONSTRAINT investment_policies_status_check
        CHECK (policy_status IN ('DRAFT', 'CONFIRMED', 'RETIRED')),
    ADD CONSTRAINT investment_policies_drawdown_check
        CHECK (max_drawdown_limit IS NULL OR max_drawdown_limit > 0 AND max_drawdown_limit <= 100),
    ADD CONSTRAINT investment_policies_single_position_check
        CHECK (single_position_limit IS NULL OR single_position_limit BETWEEN 0 AND 100),
    ADD CONSTRAINT investment_policies_equity_target_check
        CHECK (equity_target_pct IS NULL OR equity_target_pct BETWEEN 0 AND 100),
    ADD CONSTRAINT investment_policies_bond_target_check
        CHECK (bond_target_pct IS NULL OR bond_target_pct BETWEEN 0 AND 100),
    ADD CONSTRAINT investment_policies_gold_target_check
        CHECK (gold_target_pct IS NULL OR gold_target_pct BETWEEN 0 AND 100),
    ADD CONSTRAINT investment_policies_cash_target_check
        CHECK (cash_target_pct IS NULL OR cash_target_pct BETWEEN 0 AND 100),
    ADD CONSTRAINT investment_policies_rebalance_check
        CHECK (rebalance_tolerance_pct IS NULL OR rebalance_tolerance_pct BETWEEN 0 AND 100),
    ADD CONSTRAINT investment_policies_effective_dates_check
        CHECK (effective_to IS NULL OR effective_to >= effective_from);

-- Binding codes are one-time capabilities; consumption is separate from
-- expiration so concurrent replays can be rejected atomically.
ALTER TABLE identity.channel_bindings
    ADD COLUMN IF NOT EXISTS token_consumed_at TIMESTAMPTZ;

-- Operational fields required by a durable Outbox consumer.
ALTER TABLE workflow.outbox
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS locked_by TEXT,
    ADD COLUMN IF NOT EXISTS external_message_id TEXT;

ALTER TABLE risk.alert_events
    ADD COLUMN IF NOT EXISTS dedupe_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_events_dedupe
    ON risk.alert_events(tenant_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;

ALTER TABLE reporting.reports
    ADD COLUMN IF NOT EXISTS job_run_id UUID;
ALTER TABLE reporting.reports
    ADD CONSTRAINT fk_reports_tenant_job_run
    FOREIGN KEY (tenant_id, job_run_id)
    REFERENCES workflow.job_runs(tenant_id, id) ON DELETE RESTRICT;

-- Older schemas encoded 15:00 as a universal market-calendar default. Product
-- and sales-channel rules can differ, so the market field is only an optional
-- fallback and the effective rule is versioned per instrument/channel.
ALTER TABLE reference.trading_calendars
    ALTER COLUMN fund_cutoff_time DROP DEFAULT;
CREATE TABLE IF NOT EXISTS reference.instrument_cutoff_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id) ON DELETE CASCADE,
    sales_channel TEXT NOT NULL DEFAULT 'DEFAULT',
    cutoff_time TIME NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    effective_from DATE NOT NULL,
    effective_to DATE,
    source_id UUID REFERENCES reference.data_sources(id),
    rule_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    UNIQUE (instrument_id, sales_channel, effective_from, rule_version)
);

-- Missing quality metadata must fail closed rather than silently becoming
-- FRESH. Existing rows are preserved; all future omitted values are unavailable.
ALTER TABLE ledger.position_snapshots ALTER COLUMN data_quality_status SET DEFAULT 'UNAVAILABLE';
ALTER TABLE ledger.account_nav_snapshots ALTER COLUMN data_quality_status SET DEFAULT 'UNAVAILABLE';
ALTER TABLE market.source_snapshots ALTER COLUMN quality_status SET DEFAULT 'UNAVAILABLE';
ALTER TABLE market.market_observations ALTER COLUMN quality_status SET DEFAULT 'UNAVAILABLE';
ALTER TABLE research.evidence_refs ALTER COLUMN quality_status SET DEFAULT 'UNAVAILABLE';
ALTER TABLE reporting.reports ALTER COLUMN quality_status SET DEFAULT 'UNAVAILABLE';
ALTER TABLE reporting.report_items ALTER COLUMN data_quality_status SET DEFAULT 'UNAVAILABLE';

ALTER TABLE ledger.account_nav_snapshots
    DROP CONSTRAINT IF EXISTS account_nav_snapshots_data_quality_status_check;
ALTER TABLE ledger.account_nav_snapshots
    ADD CONSTRAINT account_nav_snapshots_data_quality_status_check
    CHECK (data_quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'UNAVAILABLE'));
ALTER TABLE research.evidence_refs
    DROP CONSTRAINT IF EXISTS evidence_refs_quality_status_check;
ALTER TABLE research.evidence_refs
    ADD CONSTRAINT evidence_refs_quality_status_check
    CHECK (quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'CONFLICT', 'UNAVAILABLE', 'REVISED'));
ALTER TABLE reporting.report_items
    DROP CONSTRAINT IF EXISTS report_items_data_quality_status_check;
ALTER TABLE reporting.report_items
    ADD CONSTRAINT report_items_data_quality_status_check
    CHECK (data_quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'UNAVAILABLE'));

-- Database-level append-only protection. Pending events may transition status,
-- but their economic identity cannot be rewritten. Confirmed/reversed events
-- require a new REVERSAL/correction event.
CREATE OR REPLACE FUNCTION ledger.protect_ledger_event()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'ledger events are append-only; use a reversal event';
    END IF;
    IF OLD.status IN ('CONFIRMED', 'REVERSED') THEN
        RAISE EXCEPTION 'confirmed ledger events are immutable; use a reversal event';
    END IF;
    IF ROW(
        NEW.tenant_id, NEW.account_id, NEW.event_type, NEW.trade_date,
        NEW.occurred_at, NEW.source_id, NEW.external_event_id,
        NEW.idempotency_key, NEW.reverses_event_id
    ) IS DISTINCT FROM ROW(
        OLD.tenant_id, OLD.account_id, OLD.event_type, OLD.trade_date,
        OLD.occurred_at, OLD.source_id, OLD.external_event_id,
        OLD.idempotency_key, OLD.reverses_event_id
    ) THEN
        RAISE EXCEPTION 'ledger economic fields are immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS protect_ledger_event_trigger ON ledger.ledger_events;
CREATE TRIGGER protect_ledger_event_trigger
BEFORE UPDATE OR DELETE ON ledger.ledger_events
FOR EACH ROW EXECUTE FUNCTION ledger.protect_ledger_event();

CREATE OR REPLACE FUNCTION audit.reject_fact_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS protect_ledger_legs_trigger ON ledger.ledger_legs;
CREATE TRIGGER protect_ledger_legs_trigger
BEFORE UPDATE OR DELETE ON ledger.ledger_legs
FOR EACH ROW EXECUTE FUNCTION audit.reject_fact_mutation();

-- Draft fills may be corrected before confirmation. Once confirmed/rejected/
-- cancelled they are historical facts and can only be superseded by a new row.
CREATE OR REPLACE FUNCTION ledger.protect_trade_fill()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'trade fills are append-only; reject or supersede the draft';
    END IF;
    IF OLD.status <> 'DRAFT' THEN
        RAISE EXCEPTION 'terminal trade fills are immutable';
    END IF;
    IF ROW(NEW.tenant_id, NEW.account_id, NEW.created_by, NEW.idempotency_key)
       IS DISTINCT FROM
       ROW(OLD.tenant_id, OLD.account_id, OLD.created_by, OLD.idempotency_key) THEN
        RAISE EXCEPTION 'trade fill ownership and idempotency fields are immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS protect_trade_fill_trigger ON ledger.trade_fills;
CREATE TRIGGER protect_trade_fill_trigger
BEFORE UPDATE OR DELETE ON ledger.trade_fills
FOR EACH ROW EXECUTE FUNCTION ledger.protect_trade_fill();

-- Decisions keep immutable content while allowing a one-way lifecycle marker.
-- A correction is a new decision linked by previous_decision_id; the old row
-- may then move from RECORDED to SUPERSEDED/RETRACTED.
CREATE OR REPLACE FUNCTION reporting.protect_decision()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = reporting, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'decisions are historical facts and cannot be deleted';
    END IF;
    IF OLD.status <> 'RECORDED' THEN
        RAISE EXCEPTION 'terminal decision status is immutable';
    END IF;
    IF ROW(
        NEW.id, NEW.tenant_id, NEW.report_id, NEW.recommendation_id,
        NEW.user_id, NEW.decision_action, NEW.reason, NEW.correction,
        NEW.decided_at, NEW.idempotency_key, NEW.previous_decision_id
    ) IS DISTINCT FROM ROW(
        OLD.id, OLD.tenant_id, OLD.report_id, OLD.recommendation_id,
        OLD.user_id, OLD.decision_action, OLD.reason, OLD.correction,
        OLD.decided_at, OLD.idempotency_key, OLD.previous_decision_id
    ) THEN
        RAISE EXCEPTION 'decision content is immutable; append a correction';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS protect_decisions_trigger ON reporting.decisions;
CREATE TRIGGER protect_decisions_trigger
BEFORE UPDATE OR DELETE ON reporting.decisions
FOR EACH ROW EXECUTE FUNCTION reporting.protect_decision();

-- Interaction identity and capability binding never change. Only OPEN may
-- transition once to a terminal state; retries observe the stored outcome.
CREATE OR REPLACE FUNCTION workflow.protect_interaction_request()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = workflow, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'interaction requests are historical workflow facts and cannot be deleted';
    END IF;
    IF OLD.status <> 'OPEN' THEN
        RAISE EXCEPTION 'terminal interaction request status is immutable';
    END IF;
    IF ROW(
        NEW.id, NEW.tenant_id, NEW.user_id, NEW.channel_binding_id,
        NEW.report_id, NEW.recommendation_id, NEW.request_type,
        NEW.allowed_actions, NEW.action_token_hash, NEW.token_expires_at,
        NEW.payload_schema_version, NEW.idempotency_key, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.id, OLD.tenant_id, OLD.user_id, OLD.channel_binding_id,
        OLD.report_id, OLD.recommendation_id, OLD.request_type,
        OLD.allowed_actions, OLD.action_token_hash, OLD.token_expires_at,
        OLD.payload_schema_version, OLD.idempotency_key, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'interaction request identity and capability fields are immutable';
    END IF;
    IF NEW.status = 'OPEN' AND ROW(NEW.consumed_action, NEW.resolved_at)
       IS DISTINCT FROM ROW(OLD.consumed_action, OLD.resolved_at) THEN
        RAISE EXCEPTION 'open interaction request cannot contain a resolution';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS protect_interaction_requests_trigger ON workflow.interaction_requests;
CREATE TRIGGER protect_interaction_requests_trigger
BEFORE UPDATE OR DELETE ON workflow.interaction_requests
FOR EACH ROW EXECUTE FUNCTION workflow.protect_interaction_request();

DROP TRIGGER IF EXISTS protect_audit_log_trigger ON audit.audit_log;
CREATE TRIGGER protect_audit_log_trigger
BEFORE UPDATE OR DELETE ON audit.audit_log
FOR EACH ROW EXECUTE FUNCTION audit.reject_fact_mutation();
