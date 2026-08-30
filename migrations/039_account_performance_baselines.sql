-- =============================================================================
-- WealthSail Migration 039: append-only account performance baselines
--
-- A current-position import knows remaining cost but cannot reconstruct every
-- historical deposit, withdrawal or realised trade result.  Establish a
-- separate account-level tracking baseline so cumulative PnL can be calculated
-- from observed NAV and external cash flows without rewriting ledger facts.
-- =============================================================================

CREATE TABLE ledger.account_performance_baselines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL,
    baseline_as_of DATE NOT NULL,
    baseline_total_asset NUMERIC(28, 8) NOT NULL,
    initial_cumulative_pnl NUMERIC(28, 8) NOT NULL DEFAULT 0,
    source TEXT NOT NULL CHECK (source IN ('AUTO_NAV', 'AUTO_MIGRATION', 'USER')),
    version INTEGER NOT NULL CHECK (version > 0),
    created_by UUID,
    idempotency_key TEXT NOT NULL CHECK (
        length(btrim(idempotency_key)) BETWEEN 1 AND 128
    ),
    request_hash CHAR(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, account_id, version),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, account_id)
        REFERENCES identity.accounts(tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, created_by)
        REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT,
    CHECK (
        (source = 'USER' AND created_by IS NOT NULL)
        OR (source IN ('AUTO_NAV', 'AUTO_MIGRATION') AND created_by IS NULL)
    )
);

CREATE INDEX idx_account_performance_baselines_latest
    ON ledger.account_performance_baselines(
        tenant_id, account_id, version DESC, created_at DESC, id DESC
    );

-- Summary reads touch only confirmed external flows after one account's
-- baseline.  Keep that lateral aggregate and reversal exclusion index-backed.
CREATE INDEX idx_ledger_events_performance_cash_flow
    ON ledger.ledger_events(tenant_id, account_id, trade_date, id)
    WHERE status = 'CONFIRMED'
      AND event_type IN ('CASH_IN', 'CASH_OUT');

CREATE INDEX idx_ledger_events_confirmed_reversal
    ON ledger.ledger_events(tenant_id, reverses_event_id)
    WHERE status = 'CONFIRMED'
      AND event_type = 'REVERSAL'
      AND reverses_event_id IS NOT NULL;

CREATE FUNCTION ledger.protect_account_performance_baseline()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'performance baselines are append-only';
END;
$$;

CREATE TRIGGER protect_account_performance_baselines_trigger
BEFORE UPDATE OR DELETE ON ledger.account_performance_baselines
FOR EACH ROW EXECUTE FUNCTION ledger.protect_account_performance_baseline();

-- Existing accounts start tracking at their latest complete NAV.  Historical
-- PnL defaults to zero and remains optional; the user can append a reviewed
-- version later without changing this tracking date or baseline total asset.
WITH latest_nav AS (
    SELECT DISTINCT ON (nav.tenant_id, nav.account_id)
           nav.tenant_id, nav.account_id, nav.as_of, nav.total_nav
      FROM ledger.account_nav_snapshots nav
      JOIN identity.accounts account
        ON account.tenant_id = nav.tenant_id AND account.id = nav.account_id
     WHERE nav.total_nav IS NOT NULL AND account.status = 'ACTIVE'
     ORDER BY nav.tenant_id, nav.account_id, nav.as_of DESC, nav.created_at DESC
)
INSERT INTO ledger.account_performance_baselines(
    tenant_id, account_id, baseline_as_of, baseline_total_asset,
    initial_cumulative_pnl, source, version, created_by,
    idempotency_key, request_hash
)
SELECT latest_nav.tenant_id,
       latest_nav.account_id,
       latest_nav.as_of,
       latest_nav.total_nav,
       0,
       'AUTO_MIGRATION',
       1,
       NULL,
       'AUTO-MIGRATION-039:' || latest_nav.account_id::text,
       encode(
           public.digest(
               concat_ws(
                   ':', latest_nav.tenant_id::text,
                   latest_nav.account_id::text,
                   latest_nav.as_of::text,
                   latest_nav.total_nav::text,
                   'AUTO_MIGRATION'
               ),
               'sha256'
           ),
           'hex'
       )
  FROM latest_nav;

-- Every future account gets the same zero-touch behavior when its first
-- complete NAV is persisted, regardless of whether API or Worker produced it.
CREATE FUNCTION ledger.initialize_account_performance_baseline()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_catalog
AS $$
BEGIN
    IF NEW.total_nav IS NULL THEN
        RETURN NEW;
    END IF;

    INSERT INTO ledger.account_performance_baselines(
        tenant_id, account_id, baseline_as_of, baseline_total_asset,
        initial_cumulative_pnl, source, version, created_by,
        idempotency_key, request_hash
    ) VALUES (
        NEW.tenant_id,
        NEW.account_id,
        NEW.as_of,
        NEW.total_nav,
        0,
        'AUTO_NAV',
        1,
        NULL,
        'AUTO-NAV:' || NEW.account_id::text || ':' || NEW.as_of::text,
        encode(
            public.digest(
                concat_ws(
                    ':', NEW.tenant_id::text, NEW.account_id::text,
                    NEW.as_of::text, NEW.total_nav::text, 'AUTO_NAV'
                ),
                'sha256'
            ),
            'hex'
        )
    )
    ON CONFLICT (tenant_id, account_id, version) DO NOTHING;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION ledger.initialize_account_performance_baseline()
FROM PUBLIC, authenticated, wealthsail_api, wealthsail_worker;

CREATE TRIGGER initialize_account_performance_baseline_trigger
AFTER INSERT OR UPDATE OF total_nav ON ledger.account_nav_snapshots
FOR EACH ROW EXECUTE FUNCTION ledger.initialize_account_performance_baseline();

ALTER TABLE ledger.account_performance_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.account_performance_baselines FORCE ROW LEVEL SECURITY;

CREATE POLICY performance_baselines_api_select
ON ledger.account_performance_baselines
FOR SELECT TO wealthsail_api
USING (identity.is_tenant_member(tenant_id));

CREATE POLICY performance_baselines_api_insert
ON ledger.account_performance_baselines
FOR INSERT TO wealthsail_api
WITH CHECK (
    source = 'USER'
    AND created_by = auth.uid()
    AND identity.has_tenant_role(
        tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']
    )
    AND EXISTS (
        SELECT 1
          FROM identity.account_members owner
         WHERE owner.tenant_id = account_performance_baselines.tenant_id
           AND owner.account_id = account_performance_baselines.account_id
           AND owner.user_id = auth.uid()
           AND owner.effective_to IS NULL
           AND owner.relationship IN ('BENEFICIAL_OWNER', 'JOINT_OWNER')
    )
);

REVOKE ALL ON ledger.account_performance_baselines
FROM PUBLIC, authenticated, wealthsail_worker;
GRANT SELECT, INSERT ON ledger.account_performance_baselines TO wealthsail_api;

COMMENT ON TABLE ledger.account_performance_baselines IS
    'Append-only account tracking baselines and optional user-reviewed historical cumulative PnL.';
COMMENT ON COLUMN ledger.account_performance_baselines.initial_cumulative_pnl IS
    'Historical cumulative PnL known at baseline_as_of; defaults to zero and never means a return percentage.';
