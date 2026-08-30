-- =============================================================================
-- WealthSail Migration 037: append-only post-import holding-start decisions
--
-- A confirmed source import and its ledger effects stay immutable.  When a
-- user later confirms or corrects the inferred start of the current holding
-- episode, record a separate auditable decision instead of rewriting the
-- confirmed import item.  The Worker consumes the newest decision and rebuilds
-- NAV history, range metrics, position valuation and account NAV projections.
-- =============================================================================

CREATE TABLE ledger.position_holding_start_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL,
    instrument_id UUID NOT NULL
        REFERENCES reference.instruments(id) ON DELETE RESTRICT,
    source_binding_id UUID NOT NULL,
    started_on DATE NOT NULL,
    decided_by UUID NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (
        length(btrim(idempotency_key)) BETWEEN 1 AND 128
    ),
    request_hash CHAR(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, account_id)
        REFERENCES identity.accounts(tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, decided_by)
        REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, source_binding_id, account_id)
        REFERENCES ledger.portfolio_source_bindings(tenant_id, id, account_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_holding_start_decisions_current
    ON ledger.position_holding_start_decisions (
        tenant_id, account_id, instrument_id, source_binding_id,
        created_at DESC, id DESC
    );

CREATE FUNCTION ledger.protect_holding_start_decision()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'holding-start decisions are append-only';
END;
$$;

CREATE TRIGGER protect_holding_start_decisions_trigger
BEFORE UPDATE OR DELETE ON ledger.position_holding_start_decisions
FOR EACH ROW EXECUTE FUNCTION ledger.protect_holding_start_decision();

ALTER TABLE ledger.position_holding_start_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.position_holding_start_decisions FORCE ROW LEVEL SECURITY;

CREATE POLICY holding_start_decisions_api_select
ON ledger.position_holding_start_decisions
FOR SELECT TO wealthsail_api
USING (identity.is_tenant_member(tenant_id));

CREATE POLICY holding_start_decisions_api_insert
ON ledger.position_holding_start_decisions
FOR INSERT TO wealthsail_api
WITH CHECK (
    identity.has_tenant_role(
        tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']
    )
    AND decided_by = auth.uid()
);

REVOKE ALL ON ledger.position_holding_start_decisions
FROM PUBLIC, authenticated;

GRANT SELECT, INSERT ON ledger.position_holding_start_decisions TO wealthsail_api;
GRANT SELECT ON ledger.position_holding_start_decisions TO wealthsail_worker;

COMMENT ON TABLE ledger.position_holding_start_decisions IS
    'Append-only user confirmations/corrections of inferred current holding-episode start dates.';
