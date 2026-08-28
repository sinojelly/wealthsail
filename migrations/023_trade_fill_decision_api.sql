-- =============================================================================
-- WealthSail Migration 023: authenticated trade-fill and research-decision API
-- =============================================================================

-- Preserve who performed the high-risk confirmation independently from the
-- user who originally created the draft.
ALTER TABLE ledger.trade_fills
    ADD COLUMN IF NOT EXISTS confirmed_by UUID;

UPDATE ledger.trade_fills
SET confirmed_by = created_by
WHERE status = 'CONFIRMED' AND confirmed_by IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_trade_fills_confirmed_by'
          AND conrelid = 'ledger.trade_fills'::regclass
    ) THEN
        ALTER TABLE ledger.trade_fills
            ADD CONSTRAINT fk_trade_fills_confirmed_by
            FOREIGN KEY (confirmed_by)
            REFERENCES identity.profiles(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'trade_fills_confirmation_actor_check'
          AND conrelid = 'ledger.trade_fills'::regclass
    ) THEN
        ALTER TABLE ledger.trade_fills
            ADD CONSTRAINT trade_fills_confirmation_actor_check
            CHECK (
                (status = 'CONFIRMED' AND confirmed_by IS NOT NULL)
                OR (status <> 'CONFIRMED' AND confirmed_by IS NULL)
            );
    END IF;
END $$;

ALTER TABLE reporting.decisions
    ADD COLUMN IF NOT EXISTS retracted_reason TEXT,
    ADD COLUMN IF NOT EXISTS retracted_at TIMESTAMPTZ;

-- The API role receives only the columns required by the two state machines.
-- Browser/JWT clients remain read-only and cannot manufacture financial facts.
GRANT USAGE ON SCHEMA ledger, reporting, audit TO wealthsail_api;
GRANT SELECT, INSERT ON ledger.trade_fills TO wealthsail_api;
REVOKE UPDATE ON ledger.trade_fills FROM wealthsail_api;
GRANT UPDATE (status, ledger_event_id, confirmed_at, confirmed_by)
    ON ledger.trade_fills TO wealthsail_api;

GRANT SELECT, INSERT ON reporting.decisions TO wealthsail_api;
REVOKE UPDATE ON reporting.decisions FROM wealthsail_api;
GRANT UPDATE (status, retracted_reason, retracted_at)
    ON reporting.decisions TO wealthsail_api;

DROP POLICY IF EXISTS domain_api_insert ON ledger.trade_fills;
CREATE POLICY domain_api_insert
ON ledger.trade_fills
FOR INSERT TO wealthsail_api
WITH CHECK (
    created_by = auth.uid()
    AND identity.has_tenant_role(
        tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']
    )
);

DROP POLICY IF EXISTS domain_api_update ON ledger.trade_fills;
CREATE POLICY domain_api_update
ON ledger.trade_fills
FOR UPDATE TO wealthsail_api
USING (
    identity.has_tenant_role(
        tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']
    )
)
WITH CHECK (
    identity.has_tenant_role(
        tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']
    )
);

DROP POLICY IF EXISTS domain_api_insert ON reporting.decisions;
CREATE POLICY domain_api_insert
ON reporting.decisions
FOR INSERT TO wealthsail_api
WITH CHECK (
    user_id = auth.uid()
    AND identity.is_tenant_member(tenant_id)
);

DROP POLICY IF EXISTS domain_api_update ON reporting.decisions;
CREATE POLICY domain_api_update
ON reporting.decisions
FOR UPDATE TO wealthsail_api
USING (
    user_id = auth.uid()
    AND identity.is_tenant_member(tenant_id)
)
WITH CHECK (
    user_id = auth.uid()
    AND identity.is_tenant_member(tenant_id)
);

-- Draft contents are immutable. Only the confirmation state and its audit
-- linkage can change once, from DRAFT to a terminal state.
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
    IF ROW(
        NEW.id, NEW.tenant_id, NEW.account_id, NEW.instrument_id,
        NEW.decision_id, NEW.side, NEW.quantity, NEW.unit_price, NEW.fee,
        NEW.trade_date, NEW.occurred_at, NEW.source_type,
        NEW.external_fill_id, NEW.created_by, NEW.idempotency_key, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.id, OLD.tenant_id, OLD.account_id, OLD.instrument_id,
        OLD.decision_id, OLD.side, OLD.quantity, OLD.unit_price, OLD.fee,
        OLD.trade_date, OLD.occurred_at, OLD.source_type,
        OLD.external_fill_id, OLD.created_by, OLD.idempotency_key, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'trade fill content is immutable';
    END IF;
    IF NEW.status = 'CONFIRMED' THEN
        IF NEW.ledger_event_id IS NULL OR NEW.confirmed_at IS NULL
           OR NEW.confirmed_by IS NULL THEN
            RAISE EXCEPTION 'confirmed trade fill requires event, time and actor';
        END IF;
    ELSIF NEW.ledger_event_id IS NOT NULL OR NEW.confirmed_at IS NOT NULL
          OR NEW.confirmed_by IS NOT NULL THEN
        RAISE EXCEPTION 'unconfirmed trade fill cannot carry confirmation facts';
    END IF;
    RETURN NEW;
END;
$$;

-- Decision content remains immutable; retract metadata is a one-way lifecycle
-- marker rather than a rewrite of the original reason.
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
    IF NEW.status = 'RETRACTED' AND NEW.retracted_at IS NULL THEN
        RAISE EXCEPTION 'retracted decision requires a retraction timestamp';
    END IF;
    IF NEW.status <> 'RETRACTED'
       AND (NEW.retracted_reason IS NOT NULL OR NEW.retracted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'only retracted decisions may carry retraction metadata';
    END IF;
    RETURN NEW;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_trade_fills_tenant_created
    ON ledger.trade_fills (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_tenant_decided
    ON reporting.decisions (tenant_id, decided_at DESC);
