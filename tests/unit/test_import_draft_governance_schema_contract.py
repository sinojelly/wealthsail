"""Static contract for the import-draft governance forward migration."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "031_import_draft_governance.sql"


def test_031_migration_adds_terminal_reason_and_supersession_chain() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for token in (
        "terminal_reason",
        "USER_CANCELLED",
        "SUPERSEDED_BY_REPARSE",
        "EXPIRED",
        "supersedes_batch_id",
        "FOREIGN KEY (tenant_id, supersedes_batch_id)",
        "CHECK (supersedes_batch_id IS NULL OR supersedes_batch_id <> id)",
    ):
        assert token in sql


def test_031_migration_requires_a_reason_only_for_cancelled_tiantian_batches() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "status = 'CANCELLED' AND terminal_reason IS NOT NULL" in sql
    assert "status <> 'CANCELLED' AND terminal_reason IS NULL" in sql
    assert "SET terminal_reason = 'USER_CANCELLED'" in sql


def test_031_backfill_temporarily_bypasses_only_the_terminal_batch_guard() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    disable = (
        "DISABLE TRIGGER protect_portfolio_import_batches_trigger"
    )
    backfill = "SET terminal_reason = 'USER_CANCELLED'"
    enable = "ENABLE TRIGGER protect_portfolio_import_batches_trigger"

    assert disable in sql
    assert enable in sql
    assert sql.index(disable) < sql.index(backfill) < sql.index(enable)
    assert sql.count(disable) == 1
    assert sql.count(enable) == 1
    assert "DISABLE TRIGGER ALL" not in sql


def test_031_migration_grants_only_the_new_columns_to_the_api_role() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert (
        "GRANT UPDATE (terminal_reason, supersedes_batch_id)\n"
        "    ON ledger.portfolio_import_batches TO wealthsail_api"
    ) in sql
    assert "GRANT UPDATE ON ledger.portfolio_import_batches" not in sql


def test_migration_runner_reads_back_draft_governance_contract() -> None:
    runner = (ROOT / "scripts" / "migrate.py").read_text(encoding="utf-8")

    for token in (
        "terminal_reason",
        "supersedes_batch_id",
        "portfolio_import_batches_terminal_reason_check",
        "fk_portfolio_import_batches_supersedes",
        "api_import_terminal_reason_update",
    ):
        assert token in runner
