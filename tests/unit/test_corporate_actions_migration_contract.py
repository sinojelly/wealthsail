"""Contract tests for corporate-action multi-source migration 058."""

import inspect
from pathlib import Path

from scripts.migrate import _readback, load_migrations


def test_058_remains_in_the_forward_only_migration_chain() -> None:
    root = Path(__file__).resolve().parents[2]

    migrations = load_migrations(root / "migrations")

    assert any(
        migration.version == 58
        and migration.path.name == "058_corporate_actions_multisource_sync.sql"
        for migration in migrations
    )


def test_058_adds_auditable_quality_and_controlled_worker_write_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "058_corporate_actions_multisource_sync.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "ADD COLUMN IF NOT EXISTS quality_status TEXT",
        "ADD COLUMN IF NOT EXISTS verified_by TEXT[]",
        "corporate_actions_quality_status_check",
        "AKSHARE_CORPORATE_ACTIONS",
        "BAOSTOCK_CORPORATE_ACTIONS",
        "CREATE OR REPLACE FUNCTION market.upsert_corporate_action_sync",
        "SECURITY DEFINER",
        "GRANT EXECUTE ON FUNCTION market.upsert_corporate_action_sync",
        "TO wealthsail_worker",
        "REVOKE ALL ON market.corporate_actions",
        "FROM PUBLIC, authenticated, wealthsail_worker",
    ):
        assert contract in migration

    assert "GRANT INSERT ON market.corporate_actions TO wealthsail_worker" not in migration
    assert "GRANT UPDATE ON market.corporate_actions TO wealthsail_worker" not in migration
    assert "GRANT DELETE ON market.corporate_actions TO wealthsail_worker" not in migration


def test_059_grants_worker_only_the_reads_needed_for_held_instrument_sync() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "059_corporate_actions_sync_read_grants.sql"
    ).read_text(encoding="utf-8")

    assert "GRANT SELECT ON ledger.position_snapshots" in migration
    assert "reference.instruments" in migration
    assert "reference.instrument_aliases" in migration
    assert "TO wealthsail_worker" in migration
    assert "GRANT INSERT ON market.corporate_actions" not in migration


def test_readback_verifies_multisource_corporate_action_boundary() -> None:
    source = inspect.getsource(_readback)

    for contract in (
        "quality_status",
        "verified_by",
        "AKSHARE_CORPORATE_ACTIONS",
        "BAOSTOCK_CORPORATE_ACTIONS",
        "upsert_corporate_action_sync",
        "corporate action multi-source contract is missing or unsafe",
    ):
        assert contract in source
