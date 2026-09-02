BEGIN;

-- Condition-triggered INTRADAY (盘中跟踪) alert throttle.
--
-- The worker evaluates intraday triggers every 5 minutes, but a push is only
-- delivered when a threshold is breached (single holding |day change| >= 3%
-- or portfolio |day change| >= 2%). This table enforces the product rule
-- "do not re-deliver the same alert to the same user within 30 minutes"
-- without relying on a per-process in-memory cache (the worker fleet is
-- horizontally scaled, so the throttle must live in the shared database).
--
-- alert_key distinguishes independent alerts for the same user:
--   'HOLDING:<instrument_id>'  -- one specific position breached the single
--                                holding threshold
--   'PORTFOLIO'                -- the whole portfolio breached the portfolio
--                                threshold

-- The alert is stored as a distinct report variant so it is queryable and
-- never collides with the legacy per-5-minute STANDARD INTRADAY report.
ALTER TABLE reporting.reports
    DROP CONSTRAINT IF EXISTS reports_report_variant_check,
    ADD CONSTRAINT reports_report_variant_check CHECK (
        report_variant IN (
            'STANDARD',
            'PRE_MARKET_MACRO',
            'PRE_MARKET_PERSONALIZED',
            'INTRADAY_ALERT'
        )
    ),
    DROP CONSTRAINT IF EXISTS reports_variant_audience_check,
    ADD CONSTRAINT reports_variant_audience_check CHECK (
        (report_variant = 'STANDARD' AND audience_user_id IS NULL)
        OR (report_variant = 'PRE_MARKET_MACRO' AND audience_user_id IS NULL)
        OR (
            report_variant = 'PRE_MARKET_PERSONALIZED'
            AND audience_user_id IS NOT NULL
        )
        OR (
            report_variant = 'INTRADAY_ALERT'
            AND audience_user_id IS NOT NULL
        )
    );

CREATE TABLE workflow.intraday_alert_throttle (
    tenant_id    UUID         NOT NULL REFERENCES identity.tenants(id)
                                  ON DELETE CASCADE,
    user_id      UUID         NOT NULL REFERENCES identity.profiles(id)
                                  ON DELETE CASCADE,
    alert_key    TEXT         NOT NULL,
    last_sent_at TIMESTAMPTZ  NOT NULL,
    PRIMARY KEY (tenant_id, user_id, alert_key)
);

-- Mirror the RLS posture of the other workflow.* facts: tenant members may
-- read (audited), writes are owned by the trusted worker role only.
ALTER TABLE workflow.intraday_alert_throttle ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.intraday_alert_throttle FORCE ROW LEVEL SECURITY;

CREATE POLICY intraday_alert_throttle_tenant_select
    ON workflow.intraday_alert_throttle
    FOR SELECT TO authenticated
    USING (identity.is_tenant_member(tenant_id));

GRANT SELECT ON workflow.intraday_alert_throttle TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON workflow.intraday_alert_throttle TO wealthsail_worker;

-- ACL readback excludes owner-inherent rights, so protected relations must
-- share the workflow schema's non-runtime owner.
DO $$
DECLARE
    schema_owner NAME;
BEGIN
    SELECT pg_get_userbyid(namespace.nspowner)
      INTO schema_owner
      FROM pg_namespace namespace
     WHERE namespace.nspname = 'workflow';
    EXECUTE format(
        'ALTER TABLE workflow.intraday_alert_throttle OWNER TO %I',
        schema_owner
    );
END;
$$;

COMMENT ON TABLE workflow.intraday_alert_throttle IS
    'Per-user, per-alert 30-minute cooldown for condition-triggered INTRADAY pushes.';

COMMIT;
