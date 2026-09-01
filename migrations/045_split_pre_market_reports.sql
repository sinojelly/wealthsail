BEGIN;

-- Split PRE_MARKET into tenant-wide macro and per-user personalized reports
-- without changing the public four-session contract.
ALTER TABLE reporting.reports
    DROP CONSTRAINT reports_tenant_id_trade_date_report_session_version_key,
    ADD COLUMN report_variant TEXT NOT NULL DEFAULT 'STANDARD',
    ADD COLUMN audience_user_id UUID,
    ADD CONSTRAINT reports_report_variant_check CHECK (
        report_variant IN (
            'STANDARD', 'PRE_MARKET_MACRO', 'PRE_MARKET_PERSONALIZED'
        )
    ),
    ADD CONSTRAINT reports_variant_audience_check CHECK (
        (report_variant = 'STANDARD' AND audience_user_id IS NULL)
        OR (report_variant = 'PRE_MARKET_MACRO' AND audience_user_id IS NULL)
        OR (
            report_variant = 'PRE_MARKET_PERSONALIZED'
            AND audience_user_id IS NOT NULL
        )
    ),
    ADD CONSTRAINT fk_reports_audience_profile
        FOREIGN KEY (audience_user_id)
        REFERENCES identity.profiles(id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT fk_reports_tenant_audience
        FOREIGN KEY (tenant_id, audience_user_id)
        REFERENCES identity.tenant_members(tenant_id, user_id)
        ON DELETE RESTRICT;

CREATE UNIQUE INDEX uq_reports_variant_audience_version
ON reporting.reports (
    tenant_id,
    trade_date,
    report_session,
    report_variant,
    COALESCE(
        audience_user_id,
        '00000000-0000-0000-0000-000000000000'::uuid
    ),
    version
);

REVOKE ALL ON reporting.reports, reporting.report_items FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON reporting.reports, reporting.report_items FROM authenticated;
REVOKE UPDATE, DELETE, TRUNCATE
    ON reporting.reports, reporting.report_items FROM wealthsail_api;
REVOKE ALL ON reporting.reports, reporting.report_items FROM wealthsail_worker;
GRANT SELECT, INSERT
    ON reporting.reports, reporting.report_items TO wealthsail_api;

-- Replace migration 005's tenant-wide report policies. The current_user guard
-- prevents wealthsail_api (which is a member of authenticated) from gaining
-- the browser policy in addition to its app.user_id-scoped policy.
DROP POLICY IF EXISTS tenant_member_select ON reporting.reports;
DROP POLICY IF EXISTS reports_audience_select ON reporting.reports;
DROP POLICY IF EXISTS reports_audience_api_select ON reporting.reports;

CREATE POLICY reports_audience_select ON reporting.reports
    FOR SELECT TO authenticated
    USING (
        current_user = 'authenticated'
        AND identity.is_tenant_member(tenant_id)
        AND (
            audience_user_id IS NULL
            OR audience_user_id = auth.uid()
        )
    );

CREATE POLICY reports_audience_api_select ON reporting.reports
    FOR SELECT TO wealthsail_api
    USING (
        identity.is_tenant_member(tenant_id)
        AND (
            audience_user_id IS NULL
            OR audience_user_id = NULLIF(
                current_setting('app.user_id', true), ''
            )::uuid
        )
    );

DROP POLICY IF EXISTS tenant_member_select ON reporting.report_items;
DROP POLICY IF EXISTS report_items_audience_select ON reporting.report_items;
DROP POLICY IF EXISTS report_items_audience_api_select ON reporting.report_items;

CREATE POLICY report_items_audience_select ON reporting.report_items
    FOR SELECT TO authenticated
    USING (
        current_user = 'authenticated'
        AND identity.is_tenant_member(tenant_id)
        AND EXISTS (
            SELECT 1
              FROM reporting.reports parent_report
             WHERE parent_report.tenant_id = report_items.tenant_id
               AND parent_report.id = report_items.report_id
               AND (
                   parent_report.audience_user_id IS NULL
                   OR parent_report.audience_user_id = auth.uid()
               )
        )
    );

CREATE POLICY report_items_audience_api_select ON reporting.report_items
    FOR SELECT TO wealthsail_api
    USING (
        identity.is_tenant_member(tenant_id)
        AND EXISTS (
            SELECT 1
              FROM reporting.reports parent_report
             WHERE parent_report.tenant_id = report_items.tenant_id
               AND parent_report.id = report_items.report_id
               AND (
                   parent_report.audience_user_id IS NULL
                   OR parent_report.audience_user_id = NULLIF(
                       current_setting('app.user_id', true), ''
                   )::uuid
               )
        )
    );

-- The shared macro input is generated once per trade date and never mutated.
CREATE TABLE reporting.shared_pre_market_briefs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_date DATE NOT NULL UNIQUE,
    draft JSONB NOT NULL CHECK (jsonb_typeof(draft) = 'object'),
    source_context JSONB NOT NULL CHECK (jsonb_typeof(source_context) = 'object'),
    context_hash CHAR(64) NOT NULL CHECK (context_hash ~ '^[0-9a-f]{64}$'),
    quality_status TEXT NOT NULL CHECK (
        quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'UNAVAILABLE')
    ),
    generated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE FUNCTION reporting.protect_shared_pre_market_brief()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = reporting, pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'shared pre-market briefs are append-only';
END;
$$;

CREATE TRIGGER protect_shared_pre_market_briefs_trigger
BEFORE UPDATE OR DELETE ON reporting.shared_pre_market_briefs
FOR EACH ROW EXECUTE FUNCTION reporting.protect_shared_pre_market_brief();

REVOKE ALL ON reporting.shared_pre_market_briefs FROM PUBLIC, authenticated;
GRANT SELECT, INSERT ON reporting.shared_pre_market_briefs TO wealthsail_api, wealthsail_worker;

COMMENT ON TABLE reporting.shared_pre_market_briefs IS
    'One tenant-neutral append-only macro pre-market brief per trade date.';

COMMIT;
