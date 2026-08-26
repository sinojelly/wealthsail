-- =============================================================================
-- WealthSail Migration 013: MVP trust, delivery and projection hardening
--
-- Forward-only remediation for databases that may already have applied
-- migrations 009-012.  This migration contains no LOGIN role, password,
-- demonstration user or development seed.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Dedicated runtime roles. Credentials are provisioned outside migrations.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wealthsail_api') THEN
        CREATE ROLE wealthsail_api NOLOGIN INHERIT NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wealthsail_worker') THEN
        CREATE ROLE wealthsail_worker NOLOGIN INHERIT BYPASSRLS;
    END IF;
END $$;

GRANT authenticated TO wealthsail_api;
-- The deployment login joins only wealthsail_worker. NOINHERIT prevents a
-- BYPASSRLS system session from implicitly acquiring tenant API privileges;
-- code must explicitly SET LOCAL ROLE wealthsail_api in a tenant transaction.
ALTER ROLE wealthsail_worker NOINHERIT BYPASSRLS;
GRANT wealthsail_api TO wealthsail_worker;

GRANT USAGE ON SCHEMA identity, reference, ledger, market, workflow
    TO wealthsail_worker;
GRANT SELECT ON
    identity.tenant_members,
    reference.instruments,
    reference.instrument_aliases,
    reference.data_sources,
    reference.trading_calendars,
    ledger.ledger_events,
    ledger.ledger_legs,
    ledger.position_snapshots,
    ledger.account_nav_snapshots,
    workflow.jobs
TO wealthsail_worker;
GRANT SELECT, INSERT, UPDATE ON reference.data_sources TO wealthsail_worker;
GRANT SELECT, INSERT ON market.source_snapshots, market.market_observations
    TO wealthsail_worker;

-- Browser/Data API identities must never manufacture system facts.  These
-- revokes are required for already-upgraded databases; migration 009 now grants
-- the same capabilities only to wealthsail_api on a clean installation.
REVOKE INSERT, UPDATE, DELETE ON
    research.model_runs,
    reporting.reports,
    reporting.report_items,
    workflow.jobs,
    workflow.job_runs,
    workflow.outbox,
    audit.audit_log,
    identity.binding_challenges,
    identity.channel_bindings,
    ledger.position_snapshots,
    ledger.account_nav_snapshots,
    identity.accounts,
    research.theses,
    risk.investment_policies,
    risk.alert_rules
FROM authenticated;
REVOKE UPDATE ON identity.profiles FROM authenticated;
GRANT INSERT, UPDATE, DELETE ON identity.accounts, research.theses,
    risk.investment_policies, risk.alert_rules TO wealthsail_api;
GRANT INSERT, UPDATE ON identity.channel_bindings TO wealthsail_api;
GRANT UPDATE ON identity.profiles TO wealthsail_api;

-- Remove the legacy password-hash oracle regardless of which earlier hotfix
-- name/signature was installed.
DROP FUNCTION IF EXISTS identity.authenticate_credentials(TEXT);
REVOKE SELECT (password_hash) ON identity.profiles FROM PUBLIC, authenticated;

-- API-only credential lookup.  The browser role is not a member of this role.
DROP POLICY IF EXISTS domain_api_identity_select ON identity.profiles;
CREATE POLICY domain_api_identity_select ON identity.profiles
    FOR SELECT TO wealthsail_api USING (true);
DROP POLICY IF EXISTS domain_api_membership_select ON identity.tenant_members;
CREATE POLICY domain_api_membership_select ON identity.tenant_members
    FOR SELECT TO wealthsail_api USING (true);
DROP POLICY IF EXISTS domain_api_tenant_select ON identity.tenants;
CREATE POLICY domain_api_tenant_select ON identity.tenants
    FOR SELECT TO wealthsail_api USING (true);
GRANT USAGE ON SCHEMA identity TO wealthsail_api;
GRANT SELECT (id, email, nickname, password_hash, avatar_url, created_at, updated_at)
    ON identity.profiles TO wealthsail_api;
GRANT SELECT ON identity.tenant_members, identity.tenants TO wealthsail_api;

-- -----------------------------------------------------------------------------
-- 2. Closed-pilot invitations and rotating refresh sessions.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS identity.invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    invited_email TEXT NOT NULL,
    invited_role TEXT NOT NULL CHECK (invited_role IN ('ADMIN', 'EDITOR', 'VIEWER')),
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'ACCEPTED', 'EXPIRED', 'REVOKED')),
    expires_at TIMESTAMPTZ NOT NULL,
    invited_by UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE RESTRICT,
    accepted_by UUID REFERENCES identity.profiles(id) ON DELETE RESTRICT,
    accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at),
    CHECK ((status = 'ACCEPTED') = (accepted_by IS NOT NULL AND accepted_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_invitations_pending_lookup
    ON identity.invitations (token_hash, expires_at)
    WHERE status = 'PENDING';

CREATE TABLE IF NOT EXISTS identity.refresh_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    rotated_at TIMESTAMPTZ,
    parent_session_id UUID REFERENCES identity.refresh_sessions(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS idx_refresh_sessions_active
    ON identity.refresh_sessions (token_hash, expires_at)
    WHERE revoked_at IS NULL;

ALTER TABLE identity.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.invitations FORCE ROW LEVEL SECURITY;
ALTER TABLE identity.refresh_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.refresh_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS api_invitation_access ON identity.invitations;
CREATE POLICY api_invitation_access ON identity.invitations
    FOR ALL TO wealthsail_api USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS api_refresh_session_access ON identity.refresh_sessions;
CREATE POLICY api_refresh_session_access ON identity.refresh_sessions
    FOR ALL TO wealthsail_api USING (true) WITH CHECK (true);
REVOKE ALL ON identity.invitations, identity.refresh_sessions FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, UPDATE ON identity.invitations, identity.refresh_sessions
    TO wealthsail_api;

CREATE OR REPLACE FUNCTION identity.create_invitation(
    p_actor_id UUID,
    p_tenant_id UUID,
    p_email TEXT,
    p_role TEXT,
    p_token_hash TEXT,
    p_expires_at TIMESTAMPTZ
) RETURNS TABLE (out_invitation_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, pg_temp
AS $$
DECLARE
    v_id UUID;
BEGIN
    IF p_role NOT IN ('ADMIN', 'EDITOR', 'VIEWER') OR p_expires_at <= now() THEN
        RAISE EXCEPTION 'INVITATION_INVALID';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM identity.tenant_members
         WHERE tenant_id=p_tenant_id AND user_id=p_actor_id
           AND status='ACTIVE' AND role IN ('OWNER', 'ADMIN')
    ) THEN
        RAISE EXCEPTION 'INVITATION_FORBIDDEN';
    END IF;
    IF EXISTS (SELECT 1 FROM identity.profiles WHERE lower(email)=lower(p_email)) THEN
        RAISE EXCEPTION 'EMAIL_ALREADY_REGISTERED';
    END IF;

    UPDATE identity.invitations
       SET status='REVOKED'
     WHERE tenant_id=p_tenant_id AND lower(invited_email)=lower(p_email)
       AND status='PENDING';
    INSERT INTO identity.invitations
        (tenant_id, invited_email, invited_role, token_hash, expires_at, invited_by)
    VALUES
        (p_tenant_id, lower(p_email), p_role, p_token_hash, p_expires_at, p_actor_id)
    RETURNING id INTO v_id;
    RETURN QUERY SELECT v_id;
END;
$$;

CREATE OR REPLACE FUNCTION identity.accept_invitation(
    p_token_hash TEXT,
    p_email TEXT,
    p_nickname TEXT,
    p_password_hash TEXT
) RETURNS TABLE (out_user_id UUID, out_tenant_id UUID, out_tenant_name TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, audit, pg_temp
AS $$
DECLARE
    v_inv identity.invitations%ROWTYPE;
    v_user_id UUID;
    v_tenant_name TEXT;
BEGIN
    SELECT * INTO v_inv
      FROM identity.invitations
     WHERE token_hash=p_token_hash AND status='PENDING'
       AND expires_at > now()
     FOR UPDATE;
    IF NOT FOUND OR lower(v_inv.invited_email) <> lower(p_email) THEN
        RAISE EXCEPTION 'INVITATION_INVALID';
    END IF;
    IF EXISTS (SELECT 1 FROM identity.profiles WHERE lower(email)=lower(p_email)) THEN
        RAISE EXCEPTION 'EMAIL_ALREADY_REGISTERED';
    END IF;

    INSERT INTO identity.profiles (email, nickname, password_hash)
    VALUES (lower(p_email), p_nickname, p_password_hash)
    RETURNING id INTO v_user_id;
    INSERT INTO identity.tenant_members (tenant_id, user_id, role, status)
    VALUES (v_inv.tenant_id, v_user_id, v_inv.invited_role, 'ACTIVE');
    UPDATE identity.invitations
       SET status='ACCEPTED', accepted_by=v_user_id, accepted_at=now()
     WHERE id=v_inv.id;
    SELECT name INTO v_tenant_name FROM identity.tenants WHERE id=v_inv.tenant_id;
    INSERT INTO audit.audit_log
        (tenant_id, actor_type, actor_id, action, resource_type, resource_id, details)
    VALUES
        (v_inv.tenant_id, 'USER', v_user_id::TEXT, 'INVITATION_ACCEPTED',
         'identity.invitations', v_inv.id::TEXT,
         jsonb_build_object('role', v_inv.invited_role));
    RETURN QUERY SELECT v_user_id, v_inv.tenant_id, v_tenant_name;
END;
$$;

REVOKE ALL ON FUNCTION identity.create_invitation(UUID, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ)
    FROM PUBLIC, authenticated;
REVOKE ALL ON FUNCTION identity.accept_invitation(TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION identity.create_invitation(UUID, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ)
    TO wealthsail_api;
GRANT EXECUTE ON FUNCTION identity.accept_invitation(TEXT, TEXT, TEXT, TEXT)
    TO wealthsail_api;

-- -----------------------------------------------------------------------------
-- 3. Per-binding Outbox delivery and explicit uncertain-result state.
-- -----------------------------------------------------------------------------
ALTER TABLE identity.channel_bindings
    ADD COLUMN IF NOT EXISTS external_user_ciphertext TEXT;
ALTER TABLE workflow.outbox
    ADD COLUMN IF NOT EXISTS channel_binding_id UUID
        REFERENCES identity.channel_bindings(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS claim_token UUID,
    ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;
ALTER TABLE workflow.outbox DROP CONSTRAINT IF EXISTS outbox_status_check;
ALTER TABLE workflow.outbox ADD CONSTRAINT outbox_status_check
    CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'UNKNOWN', 'DEAD', 'CANCELLED'));
CREATE INDEX IF NOT EXISTS idx_outbox_claimable
    ON workflow.outbox (status, available_at, lease_until, created_at)
    WHERE status IN ('PENDING', 'SENDING');
CREATE UNIQUE INDEX IF NOT EXISTS uq_outbox_binding_delivery
    ON workflow.outbox (tenant_id, topic, aggregate_id, channel_binding_id)
    WHERE channel_binding_id IS NOT NULL;
GRANT UPDATE (status, attempt_count, last_error_class, locked_at, locked_by,
              external_message_id, sent_at, claim_token, lease_until)
    ON workflow.outbox TO wealthsail_worker;
GRANT SELECT ON workflow.outbox, identity.channel_bindings TO wealthsail_worker;

-- -----------------------------------------------------------------------------
-- 4. Recoverable, compare-and-set JobRun leases.
-- -----------------------------------------------------------------------------
ALTER TABLE workflow.job_runs
    ADD COLUMN IF NOT EXISTS lease_token UUID,
    ADD COLUMN IF NOT EXISTS lease_owner TEXT,
    ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;
ALTER TABLE workflow.job_runs DROP CONSTRAINT IF EXISTS job_runs_status_check;
ALTER TABLE workflow.job_runs ADD CONSTRAINT job_runs_status_check
    CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'ABSTAINED', 'SKIPPED'));
CREATE INDEX IF NOT EXISTS idx_job_runs_recoverable
    ON workflow.job_runs (status, lease_until, next_retry_at)
    WHERE status IN ('PENDING', 'RUNNING', 'FAILED');
GRANT SELECT, INSERT ON workflow.job_runs TO wealthsail_worker;
GRANT UPDATE (status, started_at, finished_at, error_class, lease_token,
              lease_owner, lease_until, heartbeat_at, attempt_count, next_retry_at)
    ON workflow.job_runs TO wealthsail_worker;

-- -----------------------------------------------------------------------------
-- 5. Projection completeness: unknown valuation is NULL, never zero/omitted.
-- -----------------------------------------------------------------------------
ALTER TABLE ledger.account_nav_snapshots
    ALTER COLUMN positions_market_value DROP NOT NULL,
    ALTER COLUMN total_nav DROP NOT NULL,
    ALTER COLUMN daily_pnl DROP NOT NULL,
    ALTER COLUMN cumulative_pnl DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS known_value NUMERIC(28, 8),
    ADD COLUMN IF NOT EXISTS valuation_coverage_pct NUMERIC(7, 4),
    ADD COLUMN IF NOT EXISTS unpriced_position_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ledger.account_nav_snapshots
    DROP CONSTRAINT IF EXISTS account_nav_coverage_check;
ALTER TABLE ledger.account_nav_snapshots
    ADD CONSTRAINT account_nav_coverage_check CHECK (
        valuation_coverage_pct IS NULL OR valuation_coverage_pct BETWEEN 0 AND 100
    );
-- Projection rebuilding clears the same-day derived position rows before the
-- deterministic insert. The API role remains tenant-scoped even when a Worker
-- initiates this operation.
DROP POLICY IF EXISTS domain_api_projection_delete ON ledger.position_snapshots;
CREATE POLICY domain_api_projection_delete ON ledger.position_snapshots
    FOR DELETE TO wealthsail_api
    USING (identity.is_tenant_member(tenant_id));
GRANT DELETE ON ledger.position_snapshots TO wealthsail_api;
GRANT UPDATE (
    known_value, valuation_coverage_pct, unpriced_position_count
) ON ledger.account_nav_snapshots TO wealthsail_api;

-- The 14:30 estimator keeps the dated public disclosure separate from the
-- derived observation.  component_code is intentionally not a foreign key:
-- disclosures may contain Hong Kong or other assets that the MVP cannot price;
-- those rows must remain visible as uncovered weight instead of disappearing.
CREATE TABLE IF NOT EXISTS market.fund_disclosure_components (
    fund_instrument_id UUID NOT NULL REFERENCES reference.instruments(id) ON DELETE CASCADE,
    component_code TEXT NOT NULL,
    component_name TEXT NOT NULL,
    weight NUMERIC(12, 10) NOT NULL CHECK (weight > 0 AND weight <= 1),
    disclosed_on DATE NOT NULL,
    disclosure_label TEXT NOT NULL,
    source_id UUID REFERENCES reference.data_sources(id),
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fund_instrument_id, component_code, disclosed_on)
);
CREATE INDEX IF NOT EXISTS idx_fund_disclosure_latest
    ON market.fund_disclosure_components (fund_instrument_id, disclosed_on DESC);
ALTER TABLE market.market_observations
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
REVOKE ALL ON market.fund_disclosure_components FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, UPDATE ON market.fund_disclosure_components TO wealthsail_worker;

-- -----------------------------------------------------------------------------
-- 6. Risk-policy lifecycle metadata and report evidence links.
-- Legacy bond/gold columns remain for old data but are not accepted by the L1 API.
-- -----------------------------------------------------------------------------
ALTER TABLE risk.investment_policies
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS policy_hash TEXT,
    ADD COLUMN IF NOT EXISTS previewed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS confirmed_by UUID REFERENCES identity.profiles(id),
    ADD COLUMN IF NOT EXISTS confirmation_provenance TEXT,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS benchmark_code TEXT,
    ADD COLUMN IF NOT EXISTS default_silence_minutes INTEGER;
ALTER TABLE risk.investment_policies
    DROP CONSTRAINT IF EXISTS investment_policies_status_check;
ALTER TABLE risk.investment_policies
    ADD CONSTRAINT investment_policies_status_check
        CHECK (policy_status IN ('DRAFT', 'PREVIEW', 'CONFIRMED', 'RETIRED'));
CREATE UNIQUE INDEX IF NOT EXISTS uq_risk_policy_idempotency
    ON risk.investment_policies (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Replace migration 008's two-state trigger with the explicit
-- DRAFT/PREVIEW/CONFIRMED/RETIRED lifecycle. Content is immutable after
-- confirmation; retirement and promotion both require version+1.
CREATE OR REPLACE FUNCTION risk.protect_investment_policy()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = risk, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.policy_status NOT IN ('DRAFT', 'PREVIEW') THEN
            RAISE EXCEPTION 'confirmed/retired policies are historical facts';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'investment policy version must increment by one';
    END IF;
    IF ROW(NEW.id, NEW.tenant_id, NEW.account_id, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id, OLD.tenant_id, OLD.account_id, OLD.created_at) THEN
        RAISE EXCEPTION 'investment policy ownership fields are immutable';
    END IF;
    IF OLD.policy_status IN ('DRAFT', 'PREVIEW') THEN
        IF NEW.policy_status NOT IN ('DRAFT', 'PREVIEW', 'CONFIRMED') THEN
            RAISE EXCEPTION 'draft/preview may only advance to confirmation';
        END IF;
        IF NEW.policy_status = 'CONFIRMED'
           AND NEW.confirmation_provenance IS DISTINCT FROM 'USER_CONFIRMED' THEN
            RAISE EXCEPTION 'confirmed policy requires USER_CONFIRMED provenance';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.policy_status = 'CONFIRMED' AND NEW.policy_status = 'RETIRED' THEN
        IF ROW(
            NEW.max_drawdown_limit, NEW.single_position_limit,
            NEW.equity_target_pct, NEW.bond_target_pct, NEW.gold_target_pct,
            NEW.cash_target_pct, NEW.rebalance_tolerance_pct,
            NEW.investment_horizon_months, NEW.cash_need_amount,
            NEW.allowed_asset_types, NEW.alert_budget_per_day,
            NEW.benchmark_code, NEW.default_silence_minutes,
            NEW.policy_hash, NEW.confirmed_at, NEW.confirmed_by,
            NEW.confirmation_provenance
        ) IS DISTINCT FROM ROW(
            OLD.max_drawdown_limit, OLD.single_position_limit,
            OLD.equity_target_pct, OLD.bond_target_pct, OLD.gold_target_pct,
            OLD.cash_target_pct, OLD.rebalance_tolerance_pct,
            OLD.investment_horizon_months, OLD.cash_need_amount,
            OLD.allowed_asset_types, OLD.alert_budget_per_day,
            OLD.benchmark_code, OLD.default_silence_minutes,
            OLD.policy_hash, OLD.confirmed_at, OLD.confirmed_by,
            OLD.confirmation_provenance
        ) THEN
            RAISE EXCEPTION 'confirmed policy content is immutable; create a new preview';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'terminal investment policy status is immutable';
END;
$$;

CREATE TABLE IF NOT EXISTS reporting.report_item_evidence (
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    report_item_id UUID NOT NULL REFERENCES reporting.report_items(id) ON DELETE CASCADE,
    evidence_ref_id UUID NOT NULL REFERENCES research.evidence_refs(id) ON DELETE RESTRICT,
    claim_type TEXT NOT NULL CHECK (claim_type IN ('FACT', 'INFERENCE', 'OPINION')),
    PRIMARY KEY (tenant_id, report_item_id, evidence_ref_id)
);
ALTER TABLE reporting.report_items
    ADD CONSTRAINT uq_report_items_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE research.evidence_refs
    ADD CONSTRAINT uq_evidence_refs_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE reporting.report_item_evidence
    ADD CONSTRAINT fk_report_item_evidence_item
        FOREIGN KEY (tenant_id, report_item_id)
        REFERENCES reporting.report_items(tenant_id, id) ON DELETE CASCADE,
    ADD CONSTRAINT fk_report_item_evidence_ref
        FOREIGN KEY (tenant_id, evidence_ref_id)
        REFERENCES research.evidence_refs(tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE reporting.report_item_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE reporting.report_item_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_member_select ON reporting.report_item_evidence
    FOR SELECT TO authenticated USING (identity.is_tenant_member(tenant_id));
CREATE POLICY domain_api_report_evidence_write ON reporting.report_item_evidence
    FOR ALL TO wealthsail_api USING (identity.is_tenant_member(tenant_id))
    WITH CHECK (identity.is_tenant_member(tenant_id));
GRANT SELECT ON reporting.report_item_evidence TO authenticated;
GRANT SELECT, INSERT ON reporting.report_item_evidence TO wealthsail_api;
GRANT INSERT ON research.evidence_refs TO wealthsail_api;
CREATE POLICY domain_api_evidence_insert ON research.evidence_refs
    FOR INSERT TO wealthsail_api
    WITH CHECK (identity.is_tenant_member(tenant_id));

-- Worker-only ciphertext and refresh material must not be reachable through
-- inherited browser grants.
REVOKE SELECT (external_user_ciphertext) ON identity.channel_bindings
    FROM PUBLIC, authenticated, wealthsail_api;
GRANT SELECT (id, tenant_id, user_id, channel_type, external_user_ciphertext,
              status, version)
    ON identity.channel_bindings TO wealthsail_worker;
