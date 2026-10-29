"""Test Migration 093 contract for AI usage overlap function and RLS policies."""

from pathlib import Path


def test_migration_093_file_exists_and_replaces_overlap_function():
    root = Path(__file__).resolve().parents[2]
    migration_file = root / "migrations" / "093_fix_ai_usage_overlap_json_and_role.sql"
    assert migration_file.exists(), "093_fix_ai_usage_overlap_json_and_role.sql must exist"

    sql = migration_file.read_text(encoding="utf-8")
    # Verify function replacement and SECURITY DEFINER
    assert "CREATE OR REPLACE FUNCTION research.mark_ai_usage_overlap(p_call UUID)" in sql
    assert "SECURITY DEFINER" in sql

    # Verify worker check separates current_setting('role'), SESSION_USER and pg_has_role
    assert "current_setting('role', true) = 'wealthsail_worker'" in sql
    assert "SESSION_USER = 'wealthsail_worker'" in sql
    assert "pg_has_role(SESSION_USER, 'wealthsail_worker', 'USAGE')" in sql
    assert "pg_has_role(SESSION_USER, 'wealthsail_worker', 'MEMBER')" in sql

    # Verify NULLIF on request.jwt.claims is used to avoid invalid input syntax for type json on empty string
    assert "NULLIF(current_setting('request.jwt.claims', true), '')" in sql
    assert "42501" in sql

    # Verify grants
    assert "REVOKE ALL ON FUNCTION research.mark_ai_usage_overlap(UUID) FROM PUBLIC,authenticated;" in sql
    assert "GRANT EXECUTE ON FUNCTION research.mark_ai_usage_overlap(UUID) TO wealthsail_api,wealthsail_worker;" in sql
