"""Forward-migration contract for tenant, user and account ownership boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "032_tenant_user_account_ownership.sql"


@pytest.fixture(scope="module")
def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_separates_platform_authority_from_tenant_membership(
    migration_sql: str,
) -> None:
    """Removing platform_roles must break platform-only administration."""
    for token in (
        "CREATE TABLE identity.platform_roles",
        "PLATFORM_ADMIN",
        "SUPPORT",
        "identity.is_platform_admin",
        "REVOKE ALL ON identity.platform_roles FROM PUBLIC, authenticated",
    ):
        assert token in migration_sql
    assert "GRANT EXECUTE ON FUNCTION identity.is_platform_admin" not in migration_sql


def test_migration_persists_a_valid_default_tenant_without_granting_membership(
    migration_sql: str,
) -> None:
    """A default tenant preference must reference an existing membership."""
    for token in (
        "CREATE TABLE identity.user_preferences",
        "default_tenant_id",
        "default_asset_scope",
        "FOREIGN KEY (default_tenant_id, user_id)",
        "REFERENCES identity.tenant_members(tenant_id, user_id)",
    ):
        assert token in migration_sql


def test_migration_models_account_ownership_without_copying_it_to_positions(
    migration_sql: str,
) -> None:
    """Account membership is the ownership source for all downstream facts."""
    for token in (
        "ADD COLUMN ownership_scope",
        "UNASSIGNED",
        "PERSONAL",
        "SHARED",
        "CREATE TABLE identity.account_members",
        "BENEFICIAL_OWNER",
        "JOINT_OWNER",
        "COLLABORATOR",
        "FOREIGN KEY (tenant_id, account_id)",
        "FOREIGN KEY (tenant_id, user_id)",
        "effective_from",
        "effective_to",
    ):
        assert token in migration_sql
    assert "ALTER TABLE ledger.position_snapshots ADD COLUMN owner_user_id" not in migration_sql


def test_personal_tenant_invitation_is_platform_controlled_and_atomic(
    migration_sql: str,
) -> None:
    """A personal registration cannot exist without its tenant and OWNER membership."""
    for token in (
        "CREATE_PERSONAL_TENANT",
        "JOIN_TENANT",
        "identity.create_personal_tenant_invitation",
        "identity.is_platform_admin(p_actor_id)",
        "p_actor_id IS DISTINCT FROM auth.uid()",
        "INSERT INTO identity.tenants",
        "INSERT INTO identity.tenant_members",
        "'OWNER'",
        "INSERT INTO identity.user_preferences",
    ):
        assert token in migration_sql


def test_new_identity_tables_enforce_rls_and_minimal_runtime_grants(
    migration_sql: str,
) -> None:
    for table in (
        "identity.platform_roles",
        "identity.user_preferences",
        "identity.account_members",
    ):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in migration_sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in migration_sql
        assert f"REVOKE ALL ON {table} FROM PUBLIC, authenticated" in migration_sql
