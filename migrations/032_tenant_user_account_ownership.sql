-- =============================================================================
-- WealthSail Migration 032: tenant, user and account ownership boundaries
--
-- Tenant membership grants collaboration access.  It is deliberately not an
-- assertion of beneficial ownership.  Ownership is attached once, at account
-- level, so every position, NAV snapshot and ledger event inherits the same
-- attribution without denormalised owner columns.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Platform authority is independent from tenant authority.
-- -----------------------------------------------------------------------------
CREATE TABLE identity.platform_roles (
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('PLATFORM_ADMIN', 'SUPPORT')),
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'SUSPENDED')),
    granted_by UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE RESTRICT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    suspended_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, role),
    CHECK ((status = 'SUSPENDED') = (suspended_at IS NOT NULL))
);

ALTER TABLE identity.platform_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.platform_roles FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_roles_api_select
ON identity.platform_roles
FOR SELECT TO wealthsail_api
USING (true);

REVOKE ALL ON identity.platform_roles FROM PUBLIC, authenticated;
GRANT SELECT ON identity.platform_roles TO wealthsail_api;

CREATE OR REPLACE FUNCTION identity.is_platform_admin(target_user UUID)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = identity, pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM identity.platform_roles pr
         WHERE pr.user_id = target_user
           AND pr.role = 'PLATFORM_ADMIN'
           AND pr.status = 'ACTIVE'
    );
$$;

REVOKE ALL ON FUNCTION identity.is_platform_admin(UUID)
FROM PUBLIC, authenticated, wealthsail_api;

-- -----------------------------------------------------------------------------
-- 2. User preference chooses a tenant; it never creates membership.
-- -----------------------------------------------------------------------------
CREATE TABLE identity.user_preferences (
    user_id UUID PRIMARY KEY REFERENCES identity.profiles(id) ON DELETE CASCADE,
    default_tenant_id UUID,
    default_asset_scope TEXT NOT NULL DEFAULT 'MINE'
        CHECK (default_asset_scope IN ('MINE', 'TENANT')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (default_tenant_id, user_id)
        REFERENCES identity.tenant_members(tenant_id, user_id)
        ON DELETE RESTRICT
);

-- Existing users get a deterministic preference.  Runtime login still checks
-- ACTIVE membership and ACTIVE tenant status before trusting this value.
INSERT INTO identity.user_preferences(user_id, default_tenant_id)
SELECT picked.user_id, picked.tenant_id
  FROM (
      SELECT DISTINCT ON (tm.user_id)
             tm.user_id, tm.tenant_id
        FROM identity.tenant_members tm
        JOIN identity.tenants t ON t.id = tm.tenant_id
       WHERE tm.status = 'ACTIVE' AND t.status = 'ACTIVE'
       ORDER BY tm.user_id, tm.created_at, tm.id
  ) AS picked
ON CONFLICT (user_id) DO NOTHING;

ALTER TABLE identity.user_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.user_preferences FORCE ROW LEVEL SECURITY;
CREATE POLICY user_preferences_api_access
ON identity.user_preferences
FOR ALL TO wealthsail_api
USING (true)
WITH CHECK (true);

REVOKE ALL ON identity.user_preferences FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, UPDATE ON identity.user_preferences TO wealthsail_api;

-- -----------------------------------------------------------------------------
-- 3. Account-level beneficial ownership.
-- -----------------------------------------------------------------------------
ALTER TABLE identity.accounts
    ADD COLUMN ownership_scope TEXT NOT NULL DEFAULT 'UNASSIGNED'
        CHECK (ownership_scope IN ('UNASSIGNED', 'PERSONAL', 'SHARED'));

COMMENT ON COLUMN identity.accounts.ownership_scope IS
    'Account attribution: legacy UNASSIGNED, one owner PERSONAL, or multi-owner SHARED.';

CREATE TABLE identity.account_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    account_id UUID NOT NULL,
    user_id UUID NOT NULL,
    relationship TEXT NOT NULL CHECK (
        relationship IN ('BENEFICIAL_OWNER', 'JOINT_OWNER', 'COLLABORATOR')
    ),
    permission_role TEXT NOT NULL CHECK (
        permission_role IN ('OWNER', 'EDITOR', 'VIEWER')
    ),
    is_primary BOOLEAN NOT NULL DEFAULT false,
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to DATE,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, account_id)
        REFERENCES identity.accounts(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, created_by)
        REFERENCES identity.tenant_members(tenant_id, user_id) ON DELETE RESTRICT,
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    UNIQUE (tenant_id, id)
);

CREATE UNIQUE INDEX uq_account_members_current_user
    ON identity.account_members(account_id, user_id)
    WHERE effective_to IS NULL;
CREATE UNIQUE INDEX uq_account_members_current_primary
    ON identity.account_members(account_id)
    WHERE effective_to IS NULL AND is_primary;
CREATE INDEX idx_account_members_current_owner
    ON identity.account_members(tenant_id, user_id, account_id)
    WHERE effective_to IS NULL
      AND relationship IN ('BENEFICIAL_OWNER', 'JOINT_OWNER');

ALTER TABLE identity.account_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.account_members FORCE ROW LEVEL SECURITY;
CREATE POLICY account_members_api_select
ON identity.account_members
FOR SELECT TO wealthsail_api
USING (identity.is_tenant_member(tenant_id));
CREATE POLICY account_members_api_insert
ON identity.account_members
FOR INSERT TO wealthsail_api
WITH CHECK (
    identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN'])
    OR (
        user_id = auth.uid()
        AND created_by = auth.uid()
        AND relationship = 'BENEFICIAL_OWNER'
        AND permission_role = 'OWNER'
    )
);
CREATE POLICY account_members_api_update
ON identity.account_members
FOR UPDATE TO wealthsail_api
USING (
    identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN'])
)
WITH CHECK (
    identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN'])
);
CREATE POLICY account_members_api_delete
ON identity.account_members
FOR DELETE TO wealthsail_api
USING (
    identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN'])
);

REVOKE ALL ON identity.account_members FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON identity.account_members TO wealthsail_api;

-- -----------------------------------------------------------------------------
-- 4. Invitation purpose: join a tenant or create one controlled personal tenant.
-- -----------------------------------------------------------------------------
ALTER TABLE identity.invitations
    ALTER COLUMN tenant_id DROP NOT NULL,
    DROP CONSTRAINT IF EXISTS invitations_invited_role_check,
    ADD COLUMN purpose TEXT NOT NULL DEFAULT 'JOIN_TENANT',
    ADD COLUMN personal_tenant_name TEXT;

ALTER TABLE identity.invitations
    ADD CONSTRAINT invitations_purpose_check
        CHECK (purpose IN ('JOIN_TENANT', 'CREATE_PERSONAL_TENANT')),
    ADD CONSTRAINT invitations_role_check
        CHECK (invited_role IN ('OWNER', 'ADMIN', 'EDITOR', 'VIEWER')),
    ADD CONSTRAINT invitations_purpose_shape_check CHECK (
        (
            purpose = 'JOIN_TENANT'
            AND tenant_id IS NOT NULL
            AND invited_role IN ('ADMIN', 'EDITOR', 'VIEWER')
            AND personal_tenant_name IS NULL
        ) OR (
            purpose = 'CREATE_PERSONAL_TENANT'
            AND tenant_id IS NULL
            AND invited_role = 'OWNER'
            AND length(trim(personal_tenant_name)) BETWEEN 2 AND 64
        )
    );

CREATE OR REPLACE FUNCTION identity.create_personal_tenant_invitation(
    p_actor_id UUID,
    p_email TEXT,
    p_tenant_name TEXT,
    p_token_hash TEXT,
    p_expires_at TIMESTAMPTZ
) RETURNS TABLE (out_invitation_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = identity, pg_temp
AS $$
DECLARE
    v_id UUID;
    v_name TEXT := trim(p_tenant_name);
BEGIN
    -- Bind the claimed actor to the transaction JWT context.  Checking only
    -- the supplied id would let a compromised API query impersonate a known
    -- platform administrator when calling this SECURITY DEFINER function.
    IF p_actor_id IS DISTINCT FROM auth.uid()
       OR NOT identity.is_platform_admin(p_actor_id) THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED';
    END IF;
    IF p_expires_at <= now()
       OR length(v_name) < 2 OR length(v_name) > 64 THEN
        RAISE EXCEPTION 'INVITATION_INVALID';
    END IF;
    IF EXISTS (
        SELECT 1 FROM identity.profiles WHERE lower(email) = lower(p_email)
    ) THEN
        RAISE EXCEPTION 'EMAIL_ALREADY_REGISTERED';
    END IF;

    UPDATE identity.invitations
       SET status = 'REVOKED'
     WHERE purpose = 'CREATE_PERSONAL_TENANT'
       AND lower(invited_email) = lower(p_email)
       AND status = 'PENDING';

    INSERT INTO identity.invitations(
        tenant_id, invited_email, invited_role, token_hash, status,
        expires_at, invited_by, purpose, personal_tenant_name
    ) VALUES (
        NULL, lower(p_email), 'OWNER', p_token_hash, 'PENDING',
        p_expires_at, p_actor_id, 'CREATE_PERSONAL_TENANT', v_name
    )
    RETURNING id INTO v_id;

    RETURN QUERY SELECT v_id;
END;
$$;

REVOKE ALL ON FUNCTION identity.create_personal_tenant_invitation(
    UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ
) FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION identity.create_personal_tenant_invitation(
    UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ
) TO wealthsail_api;

-- Keep the public application contract stable while atomically handling the
-- two invitation purposes inside the protected database function.
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
            'defaultTenantSet', true
        )
    );

    RETURN QUERY SELECT v_user_id, v_tenant_id, v_tenant_name;
END;
$$;

REVOKE ALL ON FUNCTION identity.accept_invitation(TEXT, TEXT, TEXT, TEXT)
FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION identity.accept_invitation(TEXT, TEXT, TEXT, TEXT)
TO wealthsail_api;
