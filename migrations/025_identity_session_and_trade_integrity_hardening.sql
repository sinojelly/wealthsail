-- =============================================================================
-- WealthSail Migration 025: identity session and trade integrity hardening
-- =============================================================================
-- Forward-fix 022-024 without rewriting already-applied migration history:
--   * remove L1-prohibited self-registration / self-tenant RPCs;
--   * revoke access JWTs immediately after a password reset;
--   * close cross-tenant confirmation-actor and event-reuse gaps.

REVOKE ALL ON FUNCTION identity.register_with_tenant(TEXT, TEXT, TEXT, TEXT, TEXT)
FROM PUBLIC, authenticated, wealthsail_api;
DROP FUNCTION IF EXISTS identity.register_with_tenant(TEXT, TEXT, TEXT, TEXT, TEXT);

REVOKE ALL ON FUNCTION identity.create_tenant(UUID, TEXT, TEXT)
FROM PUBLIC, authenticated, wealthsail_api;
DROP FUNCTION IF EXISTS identity.create_tenant(UUID, TEXT, TEXT);

ALTER TABLE identity.profiles
    ADD COLUMN IF NOT EXISTS auth_version BIGINT NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'profiles_auth_version_positive'
          AND conrelid = 'identity.profiles'::regclass
    ) THEN
        ALTER TABLE identity.profiles
            ADD CONSTRAINT profiles_auth_version_positive
            CHECK (auth_version > 0);
    END IF;
END $$;

-- Consume exactly once, replace the password, revoke refresh sessions and
-- increment auth_version in one transaction. The API compares the version in
-- every access JWT with this current value.
CREATE OR REPLACE FUNCTION identity.reset_password(
    p_token_hash TEXT,
    p_new_password_hash TEXT
)
RETURNS TABLE(out_user_id UUID, out_email TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, audit, pg_temp
AS $$
DECLARE
    v_user_id UUID;
    v_email TEXT;
BEGIN
    IF p_token_hash IS NULL OR length(p_token_hash) <> 64
       OR p_new_password_hash IS NULL OR p_new_password_hash = '' THEN
        RAISE EXCEPTION 'RESET_TOKEN_INVALID';
    END IF;

    SELECT prt.user_id
      INTO v_user_id
      FROM identity.password_reset_tokens prt
     WHERE prt.token_hash = p_token_hash
       AND prt.consumed_at IS NULL
       AND prt.expires_at > clock_timestamp()
     FOR UPDATE;

    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'RESET_TOKEN_INVALID';
    END IF;

    UPDATE identity.profiles
       SET password_hash = p_new_password_hash,
           auth_version = auth_version + 1,
           updated_at = now()
     WHERE id = v_user_id
     RETURNING email INTO v_email;

    UPDATE identity.password_reset_tokens
       SET consumed_at = now()
     WHERE user_id = v_user_id AND consumed_at IS NULL;

    UPDATE identity.refresh_sessions
       SET revoked_at = COALESCE(revoked_at, now())
     WHERE user_id = v_user_id AND revoked_at IS NULL;

    INSERT INTO audit.audit_log(
        tenant_id, actor_type, actor_id, action, resource_type,
        resource_id, details
    )
    SELECT
        tm.tenant_id, 'USER', v_user_id, 'PASSWORD_RESET',
        'identity.profiles', v_user_id::TEXT,
        jsonb_build_object('sessionsRevoked', true, 'authVersionRotated', true)
    FROM identity.tenant_members tm
    WHERE tm.user_id = v_user_id AND tm.status = 'ACTIVE';

    RETURN QUERY SELECT v_user_id, v_email;
END;
$$;

REVOKE ALL ON FUNCTION identity.reset_password(TEXT, TEXT)
FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION identity.reset_password(TEXT, TEXT)
TO wealthsail_api;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM ledger.trade_fills fill
        LEFT JOIN identity.tenant_members member
          ON member.tenant_id = fill.tenant_id
         AND member.user_id = fill.confirmed_by
        WHERE fill.confirmed_by IS NOT NULL
          AND member.user_id IS NULL
    ) THEN
        RAISE EXCEPTION
            '025 preflight failed: trade_fills contains a cross-tenant confirmed_by';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM ledger.trade_fills
        WHERE ledger_event_id IS NOT NULL
        GROUP BY tenant_id, ledger_event_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            '025 preflight failed: one ledger event is linked by multiple trade fills';
    END IF;
END $$;

ALTER TABLE ledger.trade_fills
    DROP CONSTRAINT IF EXISTS fk_trade_fills_confirmed_by;

ALTER TABLE ledger.trade_fills
    ADD CONSTRAINT fk_trade_fills_confirmed_by_tenant
    FOREIGN KEY (tenant_id, confirmed_by)
    REFERENCES identity.tenant_members(tenant_id, user_id)
    ON DELETE RESTRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_fills_ledger_event
    ON ledger.trade_fills (tenant_id, ledger_event_id)
    WHERE ledger_event_id IS NOT NULL;

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
    AND (
        status <> 'CONFIRMED'
        OR confirmed_by = auth.uid()
    )
);
