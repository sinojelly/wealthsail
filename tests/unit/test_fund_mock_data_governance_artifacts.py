"""Safety contracts for the legacy fund mock-data audit and cleanup tools."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_fund_mock_audit_is_read_only_and_covers_real_and_legacy_facts() -> None:
    sql = (ROOT / "tools" / "audit_legacy_fund_mock_data.sql").read_text(
        encoding="utf-8"
    )

    assert "BEGIN TRANSACTION READ ONLY" in sql
    assert "ROLLBACK" in sql
    for relation in (
        "public.wealthsail_schema_migrations",
        "market.fund_lookthrough",
        "reference.fund_profiles",
        "reference.instrument_cutoff_rules",
        "market.fund_disclosure_components",
        "market.market_observations",
    ):
        assert relation in sql
    assert "DELETE FROM" not in sql.upper()
    assert "UPDATE " not in sql.upper()
    assert "INSERT INTO" not in sql.upper()


def test_fund_mock_cleanup_is_guarded_and_preserves_real_master_and_facts() -> None:
    sql = (ROOT / "tools" / "cleanup_legacy_fund_mock_data.sql").read_text(
        encoding="utf-8"
    )

    assert "apply_cleanup" in sql
    assert "cleanup_confirmation" in sql
    assert "DELETE_LEGACY_FUND_MOCK_DATA" in sql
    assert "BEGIN" in sql and "COMMIT" in sql
    assert "metadata ->> 'source' = 'index-disclosure'" in sql
    assert "source_id IS NULL" in sql
    assert "TIME '15:00'" in sql
    assert "purchase_status = 'OPEN'" in sql
    assert "redemption_status = 'OPEN'" in sql

    # Instruments and aliases are real canonical master data. Provider facts,
    # reports and migration history are evidence/audit data and must survive.
    for forbidden_target in (
        "DELETE FROM reference.instruments",
        "DELETE FROM reference.instrument_aliases",
        "DELETE FROM reference.data_sources",
        "DELETE FROM market.fund_disclosure_components",
        "DELETE FROM market.market_observations",
        "DELETE FROM market.source_snapshots",
        "DELETE FROM reporting.reports",
        "DELETE FROM reporting.report_items",
        "DELETE FROM public.wealthsail_schema_migrations",
    ):
        assert forbidden_target not in sql
