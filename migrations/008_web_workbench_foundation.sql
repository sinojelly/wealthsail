-- ==============================================================================
-- WealthSail Migration 008: Web Workbench Foundation
-- Version: v2.1.0
-- Description: Separate binding challenges from channel bindings, add atomic
--              portfolio-import drafts, and version investment policies.
--
-- Upgrade policy:
--   * additive for installations that already applied 001-007;
--   * old channel_bindings token columns remain compatibility-only and are no
--     longer written by the v2.1 Domain API;
--   * authenticated browser clients remain read-mostly; state transitions are
--     executed by the controlled Domain API after application authorization.
-- ==============================================================================

-- Fail before changing schema when legacy data has more than one current
-- policy for the same scope. Selecting one winner would silently rewrite a
-- financial control, so an operator must investigate and retire duplicates.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM risk.investment_policies
        WHERE policy_status = 'CONFIRMED'
          AND effective_to IS NULL
        GROUP BY tenant_id, account_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'migration 008 found duplicate current CONFIRMED policies; resolve them before retrying';
    END IF;
END $$;

-- 1. A binding challenge exists before an external WeCom identity is known.
-- Keeping it separate avoids inventing a placeholder external_user_hash in
-- identity.channel_bindings, where that field is intentionally NOT NULL.
CREATE TABLE identity.binding_challenges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE RESTRICT,
    channel_type TEXT NOT NULL CHECK (channel_type IN ('wecom', 'feishu', 'telegram', 'dingtalk', 'webpush')),
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'CONSUMED', 'EXPIRED', 'CANCELLED')),
    consumed_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    binding_id UUID,
    idempotency_key TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at),
    CHECK (attempt_count <= max_attempts),
    CHECK (
        (status = 'PENDING' AND consumed_at IS NULL AND resolved_at IS NULL AND binding_id IS NULL)
        OR (status = 'CONSUMED' AND consumed_at IS NOT NULL AND resolved_at IS NOT NULL AND binding_id IS NOT NULL)
        OR (status IN ('EXPIRED', 'CANCELLED') AND consumed_at IS NULL AND resolved_at IS NOT NULL AND binding_id IS NULL)
    ),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, token_hash),
    UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT fk_binding_challenges_tenant_user
        FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_binding_challenges_tenant_binding
        FOREIGN KEY (tenant_id, binding_id)
        REFERENCES identity.channel_bindings(tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX idx_binding_challenges_pending_expiry
    ON identity.binding_challenges(tenant_id, user_id, expires_at)
    WHERE status = 'PENDING';

COMMENT ON TABLE identity.binding_challenges IS
    'Short-lived one-time channel binding capabilities; raw tokens are never persisted.';
COMMENT ON COLUMN identity.channel_bindings.binding_token_hash IS
    'Deprecated compatibility column since migration 008; use identity.binding_challenges.token_hash.';
COMMENT ON COLUMN identity.channel_bindings.token_expires_at IS
    'Deprecated compatibility column since migration 008; use identity.binding_challenges.expires_at.';
COMMENT ON COLUMN identity.channel_bindings.token_consumed_at IS
    'Deprecated compatibility column since migration 008; use identity.binding_challenges.consumed_at.';
COMMENT ON COLUMN identity.channel_bindings.token_attempt_count IS
    'Deprecated compatibility column since migration 008; use identity.binding_challenges.attempt_count.';

-- 2. Initial cash and positions form one reviewed import batch, not one trade
-- fill. Confirmation creates separate economic events inside one transaction.
CREATE TABLE ledger.portfolio_import_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES identity.accounts(id) ON DELETE RESTRICT,
    created_by UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE RESTRICT,
    source_type TEXT NOT NULL CHECK (source_type IN ('MANUAL', 'CSV', 'NATURAL_LANGUAGE')),
    source_content_hash TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'CONFIRMED', 'CANCELLED')),
    idempotency_key TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    confirmed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (status = 'DRAFT' AND confirmed_at IS NULL AND cancelled_at IS NULL)
        OR (status = 'CONFIRMED' AND confirmed_at IS NOT NULL AND cancelled_at IS NULL)
        OR (status = 'CANCELLED' AND confirmed_at IS NULL AND cancelled_at IS NOT NULL)
    ),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT fk_import_batches_tenant_account
        FOREIGN KEY (tenant_id, account_id)
        REFERENCES identity.accounts(tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_import_batches_tenant_user
        FOREIGN KEY (tenant_id, created_by)
        REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT
);

CREATE TABLE ledger.portfolio_import_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    batch_id UUID NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN ('CASH_BALANCE', 'POSITION')),
    instrument_id UUID REFERENCES reference.instruments(id) ON DELETE RESTRICT,
    quantity NUMERIC(28, 10),
    unit_cost NUMERIC(28, 10),
    cash_amount NUMERIC(28, 8),
    currency CHAR(3) NOT NULL DEFAULT 'CNY',
    trade_date DATE NOT NULL,
    source_row INTEGER CHECK (source_row IS NULL OR source_row > 0),
    validation_status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW'
        CHECK (validation_status IN ('VALID', 'INVALID', 'NEEDS_REVIEW')),
    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(validation_errors) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (item_type = 'CASH_BALANCE'
            AND instrument_id IS NULL AND quantity IS NULL AND unit_cost IS NULL
            AND cash_amount IS NOT NULL AND cash_amount >= 0)
        OR
        (item_type = 'POSITION'
            AND instrument_id IS NOT NULL AND quantity IS NOT NULL AND quantity > 0
            AND unit_cost IS NOT NULL AND unit_cost >= 0 AND cash_amount IS NULL)
    ),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, batch_id, id),
    CONSTRAINT fk_import_items_tenant_batch
        FOREIGN KEY (tenant_id, batch_id)
        REFERENCES ledger.portfolio_import_batches(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX idx_import_batches_status
    ON ledger.portfolio_import_batches(tenant_id, account_id, status, updated_at DESC);
CREATE INDEX idx_import_items_batch
    ON ledger.portfolio_import_items(tenant_id, batch_id, source_row, id);

-- Imported holdings are an opening baseline, not evidence that a real cash
-- transfer or trade occurred inside WealthSail. Each reviewed item therefore
-- produces one linked ADJUSTMENT event. Real cash commands and trade fills
-- remain separate facts; the explicit provenance also makes batch reversal
-- and idempotent replay deterministic.
ALTER TABLE ledger.ledger_events
    ADD COLUMN import_batch_id UUID,
    ADD COLUMN import_item_id UUID,
    ADD CONSTRAINT ledger_events_import_origin_check CHECK (
        (import_batch_id IS NULL AND import_item_id IS NULL)
        OR (import_batch_id IS NOT NULL AND import_item_id IS NOT NULL)
    ),
    ADD CONSTRAINT fk_ledger_events_import_batch
        FOREIGN KEY (tenant_id, import_batch_id)
        REFERENCES ledger.portfolio_import_batches(tenant_id, id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_ledger_events_import_item
        FOREIGN KEY (tenant_id, import_batch_id, import_item_id)
        REFERENCES ledger.portfolio_import_items(tenant_id, batch_id, id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX uq_ledger_events_import_item_effect
    ON ledger.ledger_events(tenant_id, import_batch_id, import_item_id)
    WHERE import_batch_id IS NOT NULL AND reverses_event_id IS NULL;

-- 3. Fill out fields already specified by the canonical data design. They are
-- nullable on upgrade; the Domain API validates completeness on confirmation.
ALTER TABLE risk.investment_policies
    ADD COLUMN benchmark_instrument_id UUID REFERENCES reference.instruments(id) ON DELETE RESTRICT,
    ADD COLUMN investment_horizon_months INTEGER,
    ADD COLUMN cash_need_amount NUMERIC(28, 8),
    ADD COLUMN allowed_asset_types TEXT[],
    ADD COLUMN alert_budget_per_day INTEGER,
    ADD COLUMN version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN confirmed_at TIMESTAMPTZ,
    ADD COLUMN confirmed_by UUID REFERENCES identity.profiles(id) ON DELETE RESTRICT,
    ADD COLUMN confirmation_provenance TEXT,
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD CONSTRAINT uq_investment_policies_tenant_id UNIQUE (tenant_id, id),
    ADD CONSTRAINT investment_policies_horizon_check
        CHECK (investment_horizon_months IS NULL OR investment_horizon_months > 0),
    ADD CONSTRAINT investment_policies_cash_need_check
        CHECK (cash_need_amount IS NULL OR cash_need_amount >= 0),
    ADD CONSTRAINT investment_policies_allowed_assets_check
        CHECK (allowed_asset_types IS NULL OR (
            cardinality(allowed_asset_types) > 0
            AND array_position(allowed_asset_types, NULL) IS NULL
        )),
    ADD CONSTRAINT investment_policies_alert_budget_check
        CHECK (alert_budget_per_day IS NULL OR alert_budget_per_day >= 0),
    ADD CONSTRAINT investment_policies_version_check CHECK (version > 0),
    ADD CONSTRAINT fk_policies_tenant_confirmed_by
        FOREIGN KEY (tenant_id, confirmed_by)
        REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT;

-- Older policies have no trustworthy confirmation actor/time. Preserve that
-- uncertainty explicitly instead of inventing an owner as the confirmer. A
-- legacy current policy can then be retired, while every new confirmation is
-- required to carry real user-confirmation metadata.
UPDATE risk.investment_policies
SET confirmation_provenance = 'LEGACY_MIGRATED'
WHERE policy_status IN ('CONFIRMED', 'RETIRED');

ALTER TABLE risk.investment_policies
    ADD CONSTRAINT investment_policies_confirmation_provenance_check CHECK (
        confirmation_provenance IS NULL
        OR confirmation_provenance IN ('USER_CONFIRMED', 'LEGACY_MIGRATED')
    ),
    ADD CONSTRAINT investment_policies_confirmation_check CHECK (
        (policy_status = 'DRAFT'
            AND confirmed_at IS NULL
            AND confirmed_by IS NULL
            AND confirmation_provenance IS NULL)
        OR (policy_status IN ('CONFIRMED', 'RETIRED')
            AND confirmation_provenance = 'USER_CONFIRMED'
            AND confirmed_at IS NOT NULL
            AND confirmed_by IS NOT NULL)
        OR (policy_status IN ('CONFIRMED', 'RETIRED')
            AND confirmation_provenance = 'LEGACY_MIGRATED'
            AND confirmed_at IS NULL
            AND confirmed_by IS NULL)
    );

CREATE UNIQUE INDEX uq_investment_policies_current_confirmed
    ON risk.investment_policies(tenant_id, account_id) NULLS NOT DISTINCT
    WHERE policy_status = 'CONFIRMED' AND effective_to IS NULL;

-- 4. State-machine protection. Trusted commands still use a transaction and
-- expected-version WHERE clause; triggers are the final integrity boundary.
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
        NEW.idempotency_key, NEW.reverses_event_id,
        NEW.import_batch_id, NEW.import_item_id
    ) IS DISTINCT FROM ROW(
        OLD.tenant_id, OLD.account_id, OLD.event_type, OLD.trade_date,
        OLD.occurred_at, OLD.source_id, OLD.external_event_id,
        OLD.idempotency_key, OLD.reverses_event_id,
        OLD.import_batch_id, OLD.import_item_id
    ) THEN
        RAISE EXCEPTION 'ledger economic fields are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION identity.protect_binding_challenge()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = identity, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'binding challenges are historical security facts and cannot be deleted';
    END IF;
    IF OLD.status <> 'PENDING' THEN
        RAISE EXCEPTION 'terminal binding challenge status is immutable';
    END IF;
    IF ROW(
        NEW.id, NEW.tenant_id, NEW.user_id, NEW.channel_type, NEW.token_hash,
        NEW.expires_at, NEW.max_attempts, NEW.idempotency_key, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.id, OLD.tenant_id, OLD.user_id, OLD.channel_type, OLD.token_hash,
        OLD.expires_at, OLD.max_attempts, OLD.idempotency_key, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'binding challenge identity and capability fields are immutable';
    END IF;
    IF NEW.attempt_count < OLD.attempt_count OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'binding challenge attempts/version must move forward once';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER protect_binding_challenges_trigger
BEFORE UPDATE OR DELETE ON identity.binding_challenges
FOR EACH ROW EXECUTE FUNCTION identity.protect_binding_challenge();

CREATE OR REPLACE FUNCTION ledger.protect_portfolio_import_batch()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'confirmed/cancelled import batches are historical facts';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'DRAFT' THEN
        RAISE EXCEPTION 'terminal import batch status is immutable';
    END IF;
    IF NEW.status NOT IN ('DRAFT', 'CONFIRMED', 'CANCELLED') THEN
        RAISE EXCEPTION 'invalid import batch transition';
    END IF;
    IF NEW.status = 'CONFIRMED' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM ledger.portfolio_import_items item
            WHERE item.tenant_id = OLD.tenant_id
              AND item.batch_id = OLD.id
        ) THEN
            RAISE EXCEPTION 'an import batch cannot be confirmed without items';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM ledger.portfolio_import_items item
            WHERE item.tenant_id = OLD.tenant_id
              AND item.batch_id = OLD.id
              AND item.validation_status <> 'VALID'
        ) THEN
            RAISE EXCEPTION 'all import items must be VALID before confirmation';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM ledger.portfolio_import_items item
            WHERE item.tenant_id = OLD.tenant_id
              AND item.batch_id = OLD.id
              AND NOT EXISTS (
                  SELECT 1
                  FROM ledger.ledger_events event
                  WHERE event.tenant_id = item.tenant_id
                    AND event.import_batch_id = item.batch_id
                    AND event.import_item_id = item.id
                    AND event.event_type = 'ADJUSTMENT'
                    AND event.status = 'CONFIRMED'
                    AND event.reverses_event_id IS NULL
              )
        ) THEN
            RAISE EXCEPTION 'each import item requires one confirmed opening ADJUSTMENT event';
        END IF;
    END IF;
    IF ROW(NEW.id, NEW.tenant_id, NEW.account_id, NEW.created_by, NEW.source_type,
           NEW.source_content_hash, NEW.idempotency_key, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.tenant_id, OLD.account_id, OLD.created_by, OLD.source_type,
           OLD.source_content_hash, OLD.idempotency_key, OLD.created_at) THEN
        RAISE EXCEPTION 'import batch ownership/source/idempotency fields are immutable';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'import batch version must increment by one';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER protect_portfolio_import_batches_trigger
BEFORE UPDATE OR DELETE ON ledger.portfolio_import_batches
FOR EACH ROW EXECUTE FUNCTION ledger.protect_portfolio_import_batch();

CREATE OR REPLACE FUNCTION ledger.protect_portfolio_import_item()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
DECLARE
    parent_status TEXT;
    target_tenant UUID;
    target_batch UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_tenant := OLD.tenant_id;
        target_batch := OLD.batch_id;
    ELSE
        target_tenant := NEW.tenant_id;
        target_batch := NEW.batch_id;
    END IF;
    SELECT status INTO parent_status
    FROM ledger.portfolio_import_batches
    WHERE tenant_id = target_tenant
      AND id = target_batch
    FOR UPDATE;
    IF parent_status IS DISTINCT FROM 'DRAFT' THEN
        RAISE EXCEPTION 'import items can change only while their batch is DRAFT';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER protect_portfolio_import_items_trigger
BEFORE INSERT OR UPDATE OR DELETE ON ledger.portfolio_import_items
FOR EACH ROW EXECUTE FUNCTION ledger.protect_portfolio_import_item();

CREATE OR REPLACE FUNCTION risk.protect_investment_policy()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = risk, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.policy_status <> 'DRAFT' THEN
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
    IF OLD.policy_status = 'DRAFT' THEN
        IF NEW.policy_status NOT IN ('DRAFT', 'CONFIRMED') THEN
            RAISE EXCEPTION 'a draft policy may only remain DRAFT or become CONFIRMED';
        END IF;
        IF NEW.policy_status = 'CONFIRMED'
           AND NEW.confirmation_provenance IS DISTINCT FROM 'USER_CONFIRMED' THEN
            RAISE EXCEPTION 'a new confirmed policy requires USER_CONFIRMED provenance';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.policy_status = 'CONFIRMED' AND NEW.policy_status = 'RETIRED' THEN
        IF ROW(
            NEW.max_drawdown_limit, NEW.single_position_limit, NEW.equity_target_pct,
            NEW.bond_target_pct, NEW.gold_target_pct, NEW.cash_target_pct,
            NEW.rebalance_tolerance_pct, NEW.benchmark_instrument_id,
            NEW.investment_horizon_months, NEW.cash_need_amount,
            NEW.allowed_asset_types, NEW.alert_budget_per_day,
            NEW.effective_from, NEW.confirmed_at, NEW.confirmed_by,
            NEW.confirmation_provenance
        ) IS DISTINCT FROM ROW(
            OLD.max_drawdown_limit, OLD.single_position_limit, OLD.equity_target_pct,
            OLD.bond_target_pct, OLD.gold_target_pct, OLD.cash_target_pct,
            OLD.rebalance_tolerance_pct, OLD.benchmark_instrument_id,
            OLD.investment_horizon_months, OLD.cash_need_amount,
            OLD.allowed_asset_types, OLD.alert_budget_per_day,
            OLD.effective_from, OLD.confirmed_at, OLD.confirmed_by,
            OLD.confirmation_provenance
        ) THEN
            RAISE EXCEPTION 'confirmed policy content is immutable; create a new version';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'terminal investment policy status is immutable';
END;
$$;

CREATE TRIGGER protect_investment_policies_trigger
BEFORE UPDATE OR DELETE ON risk.investment_policies
FOR EACH ROW EXECUTE FUNCTION risk.protect_investment_policy();

-- 5. RLS and least-privilege grants for new tenant tables.
ALTER TABLE identity.binding_challenges ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.binding_challenges FORCE ROW LEVEL SECURITY;
ALTER TABLE ledger.portfolio_import_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.portfolio_import_batches FORCE ROW LEVEL SECURITY;
ALTER TABLE ledger.portfolio_import_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.portfolio_import_items FORCE ROW LEVEL SECURITY;

-- Migration 005/007 granted broad tenant-member reads and direct JWT writes
-- before the Web workbench authorization boundary was finalized. Replace
-- those policies here so browser credentials cannot bypass Domain API role,
-- re-authentication, audit, idempotency, or state-machine checks.
DROP POLICY IF EXISTS tenant_member_select ON identity.channel_bindings;
DROP POLICY IF EXISTS channel_binding_self_insert ON identity.channel_bindings;
DROP POLICY IF EXISTS channel_binding_self_update ON identity.channel_bindings;
CREATE POLICY channel_binding_self_select
ON identity.channel_bindings FOR SELECT TO authenticated
USING (user_id = auth.uid() AND identity.is_tenant_member(tenant_id));

DROP POLICY IF EXISTS tenant_editor_insert ON risk.investment_policies;
DROP POLICY IF EXISTS tenant_editor_update ON risk.investment_policies;
DROP POLICY IF EXISTS tenant_editor_delete ON risk.investment_policies;

DROP POLICY IF EXISTS tenant_member_select ON workflow.jobs;
CREATE POLICY operations_owner_admin_select
ON workflow.jobs FOR SELECT TO authenticated
USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN']));

DROP POLICY IF EXISTS tenant_member_select ON workflow.job_runs;
CREATE POLICY operations_owner_admin_select
ON workflow.job_runs FOR SELECT TO authenticated
USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN']));

DROP POLICY IF EXISTS tenant_member_select ON workflow.outbox;
CREATE POLICY operations_owner_admin_select
ON workflow.outbox FOR SELECT TO authenticated
USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN']));

DROP POLICY IF EXISTS tenant_member_select ON audit.audit_log;
CREATE POLICY operations_owner_admin_select
ON audit.audit_log FOR SELECT TO authenticated
USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN']));

CREATE POLICY binding_challenge_self_select
ON identity.binding_challenges FOR SELECT TO authenticated
USING (user_id = auth.uid() AND identity.is_tenant_member(tenant_id));

CREATE POLICY portfolio_import_batches_member_select
ON ledger.portfolio_import_batches FOR SELECT TO authenticated
USING (identity.is_tenant_member(tenant_id));

CREATE POLICY portfolio_import_items_member_select
ON ledger.portfolio_import_items FOR SELECT TO authenticated
USING (identity.is_tenant_member(tenant_id));

REVOKE ALL ON identity.binding_challenges FROM authenticated;
GRANT SELECT (
    id, tenant_id, user_id, channel_type, expires_at, attempt_count,
    max_attempts, status, consumed_at, resolved_at, binding_id, version, created_at
) ON identity.binding_challenges TO authenticated;

-- A verified binding contains routing identifiers and deprecated token fields
-- needed only by the backend adapter. Browser JWTs receive status metadata,
-- never hashes or capability internals.
REVOKE SELECT ON identity.channel_bindings FROM authenticated;
GRANT SELECT (
    id, tenant_id, user_id, channel_type, external_username, status,
    bound_at, revoked_at, last_used_at, version, created_at
) ON identity.channel_bindings TO authenticated;

REVOKE ALL ON ledger.portfolio_import_batches, ledger.portfolio_import_items FROM authenticated;
GRANT SELECT ON ledger.portfolio_import_batches, ledger.portfolio_import_items TO authenticated;

REVOKE INSERT, UPDATE ON identity.channel_bindings FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON risk.investment_policies FROM authenticated;

-- Operations pages are served by a sanitizing Domain API. Even OWNER/ADMIN
-- browser JWTs must not query raw job errors, outbox payloads, audit details,
-- or Vault/KMS reference locations directly.
REVOKE SELECT ON
    identity.secret_refs,
    workflow.jobs, workflow.job_runs, workflow.outbox,
    audit.audit_log
FROM authenticated;
