-- =============================================================================
-- WealthSail Migration 033: initialize report jobs for personal tenants
--
-- Migration 032 creates a personal tenant atomically when its one-time
-- invitation is accepted.  The scheduler reads workflow.jobs, but that path
-- did not create any rows, so a valid new tenant could never enter the four
-- governed report sessions.
--
-- Keep initialization inside the same SECURITY DEFINER transaction:
--   * only CREATE_PERSONAL_TENANT provisions schedules;
--   * JOIN_TENANT never creates a duplicate schedule set;
--   * all four jobs are present before registration commits;
--   * no INSERT privilege on workflow.jobs is granted to API/browser roles.
-- Existing tenants are deliberately not backfilled by this migration; that is
-- a separate operator-reviewed data change.
-- =============================================================================

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
    v_tenant_id UUID;
    v_tenant_name TEXT;
BEGIN
    SELECT * INTO v_inv
      FROM identity.invitations
     WHERE token_hash = p_token_hash
       AND status = 'PENDING'
       AND expires_at > now()
     FOR UPDATE;

    IF NOT FOUND OR lower(v_inv.invited_email) <> lower(p_email) THEN
        RAISE EXCEPTION 'INVITATION_INVALID';
    END IF;
    IF EXISTS (
        SELECT 1 FROM identity.profiles WHERE lower(email) = lower(p_email)
    ) THEN
        RAISE EXCEPTION 'EMAIL_ALREADY_REGISTERED';
    END IF;

    INSERT INTO identity.profiles(email, nickname, password_hash)
    VALUES (lower(p_email), p_nickname, p_password_hash)
    RETURNING id INTO v_user_id;

    IF v_inv.purpose = 'CREATE_PERSONAL_TENANT' THEN
        INSERT INTO identity.tenants(name, base_currency)
        VALUES (v_inv.personal_tenant_name, 'CNY')
        RETURNING id, name INTO v_tenant_id, v_tenant_name;

        INSERT INTO identity.tenant_members(tenant_id, user_id, role, status)
        VALUES (v_tenant_id, v_user_id, 'OWNER', 'ACTIVE');

        INSERT INTO workflow.jobs(
            tenant_id, job_name, cron_expr, report_session, is_enabled
        ) VALUES
            (v_tenant_id, 'pre_market_brief',
             '0 30 8 * * 1-5', 'PRE_MARKET', TRUE),
            (v_tenant_id, 'intraday_tracking',
             '*/5 9-14 * * 1-5', 'INTRADAY', TRUE),
            (v_tenant_id, 'fund_1430_estimate',
             '0 30 14 * * 1-5', 'FUND_1430', TRUE),
            (v_tenant_id, 'post_market_review',
             '0 30 15 * * 1-5', 'POST_MARKET', TRUE);
    ELSE
        v_tenant_id := v_inv.tenant_id;
        SELECT name INTO v_tenant_name
          FROM identity.tenants
         WHERE id = v_tenant_id AND status = 'ACTIVE';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'INVITATION_INVALID';
        END IF;

        INSERT INTO identity.tenant_members(tenant_id, user_id, role, status)
        VALUES (v_tenant_id, v_user_id, v_inv.invited_role, 'ACTIVE');
    END IF;

    INSERT INTO identity.user_preferences(
        user_id, default_tenant_id, default_asset_scope
    ) VALUES (v_user_id, v_tenant_id, 'MINE');

    UPDATE identity.invitations
       SET status = 'ACCEPTED', accepted_by = v_user_id, accepted_at = now()
     WHERE id = v_inv.id;

    INSERT INTO audit.audit_log(
        tenant_id, actor_type, actor_id, action, resource_type,
        resource_id, details
    ) VALUES (
        v_tenant_id, 'USER', v_user_id::TEXT, 'INVITATION_ACCEPTED',
        'identity.invitations', v_inv.id::TEXT,
        jsonb_build_object(
            'role', v_inv.invited_role,
            'purpose', v_inv.purpose,
            'defaultTenantSet', true,
            'reportSchedulesInitialized',
                v_inv.purpose = 'CREATE_PERSONAL_TENANT'
        )
    );

    RETURN QUERY SELECT v_user_id, v_tenant_id, v_tenant_name;
END;
$$;

REVOKE ALL ON FUNCTION identity.accept_invitation(TEXT, TEXT, TEXT, TEXT)
FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION identity.accept_invitation(TEXT, TEXT, TEXT, TEXT)
TO wealthsail_api;

COMMENT ON FUNCTION identity.accept_invitation(TEXT, TEXT, TEXT, TEXT) IS
    'Atomically accepts invitations; new personal tenants receive four enabled report jobs.';
