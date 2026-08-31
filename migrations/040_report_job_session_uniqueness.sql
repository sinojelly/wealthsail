-- =============================================================================
-- WealthSail Migration 040: one governed report job per tenant/session
--
-- The scheduler executes every enabled workflow.jobs row for a report session.
-- Duplicate session rows can therefore produce duplicate reports and pushes.
-- Refuse to guess which duplicate to keep, then enforce the invariant in the
-- database for registration, operator backfills and future management paths.
-- =============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM workflow.jobs
         WHERE report_session IS NOT NULL
         GROUP BY tenant_id, report_session
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'duplicate workflow.jobs report sessions require manual review';
    END IF;
END;
$$;

ALTER TABLE workflow.jobs
    ADD CONSTRAINT uq_jobs_tenant_report_session
    UNIQUE (tenant_id, report_session);

COMMENT ON CONSTRAINT uq_jobs_tenant_report_session ON workflow.jobs IS
    'At most one governed report job exists per tenant and report session.';
