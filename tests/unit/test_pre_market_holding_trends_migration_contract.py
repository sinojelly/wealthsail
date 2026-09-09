"""Contract tests for the pre-market holding trend cache migration."""

import inspect
from pathlib import Path

from scripts.migrate import _readback, load_migrations


def _migration_text(filename: str) -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / "migrations" / filename).read_text(encoding="utf-8")


def test_060_remains_in_the_forward_only_migration_chain() -> None:
    root = Path(__file__).resolve().parents[2]

    assert any(
        migration.version == 60
        and migration.path.name == "060_pre_market_holding_trends.sql"
        for migration in load_migrations(root / "migrations")
    )


def test_trend_cache_migration_has_one_row_per_instrument_and_day() -> None:
    sql = _migration_text("060_pre_market_holding_trends.sql")

    assert "market.instrument_trend_snapshots" in sql
    assert "UNIQUE (instrument_id, trade_date)" in sql


def test_trend_cache_mutation_is_function_only() -> None:
    sql = _migration_text("060_pre_market_holding_trends.sql")

    assert "SECURITY DEFINER" in sql
    assert "GRANT EXECUTE ON FUNCTION market.upsert_instrument_trend_snapshot" in sql
    assert "REVOKE INSERT, UPDATE, DELETE ON market.instrument_trend_snapshots" in sql


def test_readback_verifies_trend_cache_security_boundary() -> None:
    source = inspect.getsource(_readback)

    for contract in (
        "instrument_trend_snapshots",
        "upsert_instrument_trend_snapshot",
        "pre-market holding trend cache contract is missing or unsafe",
    ):
        assert contract in source
