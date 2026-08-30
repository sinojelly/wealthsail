"""Migration runner safety contract tests."""

import inspect
from pathlib import Path

from scripts.migrate import (
    _readback,
    discover_migrations,
    is_loopback_database,
    load_migrations,
    safe_target_name,
)


def test_production_migration_discovery_never_includes_dev_seeds() -> None:
    root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(root / "migrations")

    assert migrations
    assert all(path.parent.name == "migrations" for path in migrations)
    assert all("seed" not in path.name.lower() for path in migrations)
    assert any(path.name.startswith("013_") for path in migrations)


def test_production_migrations_have_one_file_per_version() -> None:
    """The deploy runner must be able to load the complete forward-only chain."""
    root = Path(__file__).resolve().parents[2]

    migrations = load_migrations(root / "migrations")

    versions = [migration.version for migration in migrations]
    assert len(versions) == len(set(versions))
    assert versions[-1] == 34
    assert migrations[-1].path.name == "034_tenant_name_management.sql"


def test_034_migration_limits_tenant_rename_to_current_owner() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "034_tenant_name_management.sql").read_text(
        encoding="utf-8"
    )

    assert "FOR UPDATE TO wealthsail_api" in migration
    assert "identity.has_tenant_role(id, ARRAY['OWNER'])" in migration
    assert "GRANT UPDATE (name, updated_at)" in migration


def test_migration_readback_verifies_tenant_rename_least_privilege() -> None:
    root = Path(__file__).resolve().parents[2]
    runner = (root / "scripts" / "migrate.py").read_text(encoding="utf-8")

    assert "tenant name update policy is missing or not owner-scoped" in runner
    assert "tenant name column privileges are missing or too broad" in runner
    assert "'identity.tenants','status','UPDATE'" in runner
    assert "'authenticated','identity.tenants','name','UPDATE'" in runner


def test_readback_covers_identity_and_trade_hardening() -> None:
    source = inspect.getsource(_readback)

    for contract in (
        "auth_version",
        "password_reset_tokens",
        "register_with_tenant",
        "create_tenant",
        "fk_trade_fills_confirmed_by_tenant",
        "uq_trade_fills_ledger_event",
        "confirmed_by = auth.uid()",
        "wecom_callback_claims",
        "claim_wecom_callback",
        "market_hot_snapshots",
    ):
        assert contract in source


def test_025_migration_contains_forward_fix_contracts() -> None:
    """The forward fix must close every capability that readback promises."""
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "025_identity_session_and_trade_integrity_hardening.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "DROP FUNCTION IF EXISTS identity.register_with_tenant",
        "DROP FUNCTION IF EXISTS identity.create_tenant",
        "auth_version = auth_version + 1",
        "fk_trade_fills_confirmed_by_tenant",
        "FOREIGN KEY (tenant_id, confirmed_by)",
        "uq_trade_fills_ledger_event",
        "confirmed_by = auth.uid()",
    ):
        assert contract in migration


def test_027_migration_enforces_worker_owned_market_hot_projection() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "027_market_hot_read_model.sql").read_text(
        encoding="utf-8"
    )

    for contract in (
        "CREATE TABLE market.market_hot_snapshots",
        "jsonb_typeof(items) = 'array'",
        "GRANT SELECT ON market.market_hot_snapshots TO wealthsail_api",
        "GRANT SELECT, INSERT ON market.market_hot_snapshots TO wealthsail_worker",
        "REVOKE ALL ON market.market_hot_snapshots FROM PUBLIC, authenticated",
    ):
        assert contract in migration


def test_028_migration_allows_only_draft_import_item_updates() -> None:
    """Confirmation row locks need UPDATE RLS without widening column grants."""
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "028_portfolio_import_item_update_policy.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "DROP POLICY IF EXISTS domain_api_update ON ledger.portfolio_import_items",
        "CREATE POLICY domain_api_update",
        "ON ledger.portfolio_import_items",
        "FOR UPDATE TO wealthsail_api",
        "identity.has_tenant_role",
        "batch.status = 'DRAFT'",
    ):
        assert contract in migration
    assert "GRANT UPDATE ON ledger.portfolio_import_items" not in migration


def test_029_migration_persists_inference_method_and_confidence() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "029_holding_start_inference_metadata.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "holding_start_method",
        "holding_start_confidence",
        "holding_start_reasons",
        "PORTFOLIO_ACTIVITY_ESTIMATE",
        "TRANSACTION_RECONCILED",
    ):
        assert contract in migration


def test_030_migration_limits_api_job_reads_to_tenant_scoped_status_columns() -> None:
    """Batch detail needs job status without exposing worker payloads broadly."""
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "030_tiantian_job_status_read_policy.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "CREATE POLICY job_runs_api_select",
        "FOR SELECT TO wealthsail_api",
        "identity.is_tenant_member(tenant_id)",
        "GRANT SELECT (id, tenant_id, status, job_type, payload, available_at)",
    ):
        assert contract in migration
    assert "GRANT SELECT ON workflow.job_runs TO wealthsail_api" not in migration


def test_readback_rejects_a_missing_import_item_update_policy() -> None:
    source = inspect.getsource(_readback)

    assert "portfolio import item UPDATE policy is missing or too broad" in source


def test_readback_checks_tenant_scoped_job_status_access() -> None:
    source = inspect.getsource(_readback)

    assert "Tiantian job status SELECT policy is missing or too broad" in source
    assert "Tiantian job status column privileges missing" in source


def test_readback_checks_tenant_user_account_ownership_boundaries() -> None:
    """Deploy success requires the new identity model and no browser write path."""
    source = inspect.getsource(_readback)

    for contract in (
        "platform_roles",
        "user_preferences",
        "account_members",
        "ownership_scope",
        "CREATE_PERSONAL_TENANT",
        "identity ownership tables expose browser writes",
        "platform personal-tenant invitation privilege boundary is invalid",
        "api_can_probe_platform_roles",
    ):
        assert contract in source


def test_033_initializes_four_jobs_only_for_a_new_personal_tenant() -> None:
    """Joining an existing tenant must not create a second schedule set."""
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "033_personal_tenant_report_jobs.sql"
    ).read_text(encoding="utf-8")

    personal_branch = migration.split(
        "IF v_inv.purpose = 'CREATE_PERSONAL_TENANT' THEN", 1
    )[1].split("ELSE", 1)[0]
    assert "INSERT INTO workflow.jobs" in personal_branch
    for session in ("PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"):
        assert personal_branch.count(f"'{session}'") == 1
    assert "GRANT INSERT ON workflow.jobs TO wealthsail_api" not in migration
    assert "GRANT INSERT ON workflow.jobs TO authenticated" not in migration


def test_readback_requires_personal_tenant_schedule_initialization() -> None:
    source = inspect.getsource(_readback)

    assert "personal tenant report schedule initialization is missing" in source


def test_safe_target_name_never_contains_password_or_query_secret() -> None:
    target = safe_target_name(
        "postgresql://deploy:super-secret@db.example:5432/wealthsail?sslmode=require"
    )

    assert target == "db.example:5432/wealthsail"
    assert "secret" not in target
    assert "deploy" not in target


def test_role_provisioning_grants_connect_to_the_selected_database() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts" / "provision_supabase_roles.sql").read_text(
        encoding="utf-8"
    )

    assert "current_database()" in source
    assert "GRANT CONNECT ON DATABASE postgres" not in source


def test_loopback_detection_cannot_be_bypassed_with_an_explicit_port() -> None:
    assert is_loopback_database("postgresql://postgres@127.0.0.1/wealthsail")
    assert is_loopback_database("postgresql://postgres@127.0.0.1:55432/wealthsail")
    assert is_loopback_database("postgresql://postgres@localhost:5432/wealthsail")
    assert is_loopback_database("postgresql://postgres@[::1]:5432/wealthsail")
    assert not is_loopback_database("postgresql://deploy@db.example:5432/wealthsail")
