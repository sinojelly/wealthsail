"""Static contract for migration 021 and its post-deploy readback."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "021_tiantian_fund_import_and_position_metrics.sql"


@pytest.fixture(scope="module")
def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "table_name",
    [
        "ledger.portfolio_source_bindings",
        "ledger.portfolio_import_source_records",
        "ledger.portfolio_source_transactions",
        "ledger.position_period_metrics",
    ],
)
def test_migration_creates_each_source_and_projection_table(
    migration_sql: str,
    table_name: str,
) -> None:
    assert f"CREATE TABLE {table_name}" in migration_sql
    assert f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY" in migration_sql
    assert f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY" in migration_sql


def test_migration_extends_batches_items_and_recoverable_jobs(migration_sql: str) -> None:
    required_tokens = {
        "TIANTIAN_FUND_ZIP",
        "POSITION_SNAPSHOT",
        "source_binding_id",
        "package_schema_version",
        "package_content_hash",
        "source_as_of",
        "parser_version",
        "preview_summary",
        "validation_status",
        "validation_errors",
        "target_account_state_hash",
        "terminal_command_type",
        "terminal_idempotency_key",
        "terminal_content_hash",
        "comparison_status",
        "candidate_holding_started_on",
        "holding_start_status",
        "user_holding_started_on",
        "source_payload",
        "FUND_NAV_BACKFILL",
        "POSITION_PERIOD_METRICS_REBUILD",
        "available_at",
        "payload",
    }

    assert not (required_tokens - set(migration_sql.replace("'", " ").split()))


def test_migration_preserves_manual_items_and_enforces_snapshot_effects(
    migration_sql: str,
) -> None:
    assert "CASH_BALANCE" in migration_sql
    assert "item.item_type IN ('CASH_BALANCE', 'POSITION')" in migration_sql
    assert "item.comparison_status IN ('NEW', 'CHANGED')" in migration_sql
    assert "item.comparison_status IN ('CHANGED', 'CLOSED')" in migration_sql
    assert "item.comparison_status = 'UNCHANGED'" in migration_sql
    assert "item.comparison_status = 'CONFLICT'" in migration_sql
    assert "each actionable import item requires its expected ledger effect" in migration_sql


def test_migration_has_tenant_foreign_keys_uniqueness_and_append_only_guards(
    migration_sql: str,
) -> None:
    required_tokens = [
        "UNIQUE (tenant_id, provider_code, external_account_key)",
        "UNIQUE (tenant_id, batch_id, record_type, external_record_id)",
        "UNIQUE (tenant_id, source_binding_id, external_transaction_id)",
        "UNIQUE (tenant_id, account_id, instrument_id, as_of)",
        "fk_source_bindings_tenant_account",
        "fk_source_records_tenant_batch",
        "fk_source_transactions_tenant_binding",
        "fk_position_metrics_tenant_account",
        "UNIQUE (tenant_id, id, account_id)",
        "FOREIGN KEY (tenant_id, source_binding_id, account_id)",
        "FOREIGN KEY (tenant_id, source_import_batch_id, source_binding_id)",
        "protect_portfolio_source_binding",
        "protect_portfolio_source_transaction",
    ]

    for token in required_tokens:
        assert token in migration_sql


def test_confirmed_tiantian_batch_requires_an_active_binding(migration_sql: str) -> None:
    assert "binding.status = 'ACTIVE'" in migration_sql
    assert "confirmed Tiantian import requires its ACTIVE account binding" in migration_sql


def test_terminal_import_commands_are_persisted_for_effect_free_idempotency(
    migration_sql: str,
) -> None:
    assert "portfolio_import_batches_terminal_command_check" in migration_sql
    assert "uq_import_batches_terminal_idempotency" in migration_sql
    assert "terminal_command_type IN ('CONFIRM', 'CANCEL')" in migration_sql
    assert (
        "GRANT UPDATE (status, confirmed_at, cancelled_at, version, updated_at,\n"
        "              terminal_command_type, terminal_idempotency_key, terminal_content_hash)"
        in migration_sql
    )
    assert (
        "GRANT UPDATE (user_holding_started_on, updated_at)\n"
        "    ON ledger.portfolio_import_items TO wealthsail_api"
        in migration_sql
    )


def test_migration_grants_only_required_api_and_worker_access(migration_sql: str) -> None:
    assert "TO wealthsail_api" in migration_sql
    assert "TO wealthsail_worker" in migration_sql
    assert "REVOKE ALL ON" in migration_sql
    assert "FROM PUBLIC, authenticated" in migration_sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON\n    ledger.position_period_metrics\nTO wealthsail_worker" in migration_sql
    assert (
        "GRANT SELECT ON\n"
        "    ledger.portfolio_source_bindings,\n"
        "    ledger.portfolio_import_batches,\n"
        "    ledger.portfolio_import_items,\n"
        "    ledger.portfolio_source_transactions\n"
        "TO wealthsail_worker"
    ) in migration_sql


def test_migration_runner_reads_back_the_new_contract() -> None:
    runner = (ROOT / "scripts" / "migrate.py").read_text(encoding="utf-8")

    for token in [
        "portfolio_source_bindings",
        "portfolio_import_source_records",
        "portfolio_source_transactions",
        "position_period_metrics",
        "TIANTIAN_FUND_ZIP",
        "POSITION_SNAPSHOT",
        "FUND_NAV_BACKFILL",
        "POSITION_PERIOD_METRICS_REBUILD",
        "relforcerowsecurity",
        "authenticated has direct writes to Tiantian import tables",
    ]:
        assert token in runner
