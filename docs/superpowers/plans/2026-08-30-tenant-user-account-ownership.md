# Tenant, User, Account Ownership and Asset Scope Implementation Plan

> **For Codex:** Execute this plan with strict TDD: write one behavior test, observe the expected failure, implement the minimum production change, then refactor and rerun the focused test.

**Goal:** Separate platform administration, tenant collaboration and account beneficial ownership; make the Dashboard default to the current user's assets; safely move Jelly into a personal tenant without changing so's portfolio facts.

**Architecture:** Extend the existing modular monolith and PostgreSQL RLS model. Tenant remains the isolation boundary, `identity.account_members` becomes the ownership attribution source, and portfolio queries receive a validated asset-scope value. Platform administration is checked from an independent database role table and never bypasses tenant RLS.

**Tech Stack:** PostgreSQL migrations/RLS, FastAPI, asyncpg, Pydantic, React 18, TanStack Query, Vitest/Testing Library, pytest.

---

## Task 1: Schema contracts for identity and ownership

**Files:**
- Create: `migrations/032_tenant_user_account_ownership.sql`
- Create: `tests/unit/test_tenant_ownership_schema_contract.py`
- Modify: `scripts/migrate.py`
- Test: `tests/unit/test_migration_runner.py`

1. Add failing schema-contract tests that apply migrations into a disposable PostgreSQL database and verify platform roles, user preferences, account ownership scope, account members and invitation-purpose constraints.
2. Add a migration-runner readback assertion for migration 032.
3. Run focused tests and observe failure because migration 032 does not exist.
4. Add the minimal forward-only migration, RLS policies, indexes, constraints, SECURITY DEFINER helpers and privileges.
5. Re-run focused tests and migration readback tests.

## Task 2: Default tenant and platform role identity behavior

**Files:**
- Modify: `backend/identity/domain_service.py`
- Modify: `backend/identity/schemas.py`
- Modify: `backend/identity/api.py`
- Modify: `backend/identity/service.py`
- Modify: `tests/unit/test_identity_service.py`
- Modify: `tests/unit/test_identity_sessions.py`

1. Add a failing login test proving a valid `default_tenant_id` wins over membership creation order.
2. Implement the query order and fallback behavior without trusting an inactive preference.
3. Add failing `get_me` and switch-tenant tests for default tenant and platform-role metadata.
4. Implement DTO and service changes; keep platform role distinct from tenant role.
5. Add tests proving a platform admin without tenant membership receives no tenant data access.

## Task 3: Personal-tenant registration invitations

**Files:**
- Modify: `backend/identity/domain_service.py`
- Modify: `backend/identity/api.py`
- Modify: `backend/identity/schemas.py`
- Modify: `frontend/src/api/identity.ts`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/features/settings/components/InvitationCard.tsx`
- Test: `tests/unit/test_identity_service.py`
- Test: `frontend/src/features/settings/settings.test.tsx`

1. Add failing tests for platform-only creation of `CREATE_PERSONAL_TENANT` invitations.
2. Add failing registration tests proving acceptance atomically creates tenant, OWNER membership and default preference.
3. Implement database functions and domain mappings with generic token errors that do not leak invitation state.
4. Add a platform-admin UI section separate from the existing “invite into current tenant” card.
5. Test unauthorized, expired, reused, wrong-email and concurrent-consumption branches.

## Task 4: Portfolio asset-scope value object and repository filtering

**Files:**
- Modify: `backend/portfolio/schemas.py`
- Modify: `backend/portfolio/api.py`
- Modify: `backend/portfolio/service.py`
- Modify: `backend/portfolio/repository.py`
- Modify: `tests/unit/test_portfolio_integrity.py`
- Modify: `tests/unit/test_position_period_metrics.py`
- Create: `tests/unit/test_portfolio_asset_scope.py`

1. Add tests for `MINE`, `TENANT`, `MEMBER` and invalid member requests.
2. Prove `MINE` includes beneficial/joint owner relationships but excludes collaborator and unassigned accounts.
3. Prove tenant aggregation starts from distinct account IDs so joint owners do not double-count.
4. Implement one validated scope value object shared by summary, positions and NAV history.
5. Return owner labels and effective scope metadata while retaining existing missing-value semantics.

## Task 5: Account ownership commands and import defaults

**Files:**
- Modify: `backend/portfolio/imports/repository.py`
- Modify: `backend/portfolio/imports/service.py`
- Modify: `backend/portfolio/commands.py`
- Create: `backend/identity/account_ownership.py`
- Modify: `backend/identity/api.py`
- Test: `tests/unit/test_portfolio_import_commands.py`
- Create: `tests/unit/test_account_ownership.py`

1. Add failing tests proving a new personal account is created with actor ownership in the same transaction.
2. Add failing tests for PERSONAL/SHARED shape validation, tenant-member validation and optimistic locking.
3. Implement account ownership commands and audit writes.
4. Preserve existing account ownership during repeat imports; never change ownership based on importer actor.
5. Verify historical accounts remain `UNASSIGNED` until explicit claim.

## Task 6: Dashboard scope UX

**Files:**
- Modify: `frontend/src/api/portfolio.ts`
- Modify: `frontend/src/api/hooks.ts`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/features/dashboard/DashboardPage.tsx`
- Create: `frontend/src/features/dashboard/components/AssetScopeSwitcher.tsx`
- Modify: `frontend/src/features/dashboard/components/HoldingsTable.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/features/dashboard/dashboard.test.tsx`
- Modify: `frontend/src/features/dashboard/dashboard-empty.test.tsx`

1. Add a failing interaction test proving the initial scope is “我的资产”.
2. Add tests for tenant/member switching, URL state and query-cache key isolation.
3. Implement the switcher and pass the scope to all portfolio hooks.
4. Add ownership labels and distinct empty states for personal-empty, tenant-empty and unassigned data.
5. Run focused accessibility and responsive-layout tests.

## Task 7: Jelly controlled migration command

**Files:**
- Create: `scripts/migrate_user_to_personal_tenant.py`
- Create: `tests/unit/test_personal_tenant_migration_command.py`
- Modify: `docs/11.数据库升级方法.md`

1. Write tests for dry-run, exact email/source-tenant preconditions, remaining OWNER guard, idempotent replay and rollback on partial failure.
2. Implement a transaction-based command using `MIGRATION_DATABASE_URL`, never the API runtime role.
3. In one transaction create Jelly's personal tenant/OWNER membership, set default tenant, grant platform admin, suspend the so membership, rotate `auth_version`, revoke refresh sessions and write audits.
4. Add a readback mode that verifies Jelly's new state and hashes/counts so's asset facts before and after.
5. Document exact dry-run, execute and readback commands with rollback guidance.

## Task 8: Verification, production rollout and Git handoff

1. Run Python lint/type checks and all pytest suites.
2. Run frontend lint, typecheck, unit tests and production build.
3. Run migration dry-run/readback against the configured environment without printing credentials.
4. Commit only project-owned files; do not stage the existing untracked architecture document or image directory.
5. Push `master`.
6. Apply migration 032 using the migration role on the server.
7. Execute Jelly migration first with `--dry-run`, then explicit `--apply`, then `--readback`.
8. Confirm Jelly must log in again, sees a personal tenant as OWNER and platform admin, cannot switch to so, and so's portfolio checksum/count is unchanged.
