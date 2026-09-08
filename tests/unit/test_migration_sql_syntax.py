"""Every migration must parse as Postgres SQL before it can reach production.

Two migrations (052/053) shipped with defects that only surfaced on the
production migrate run: one referenced a CTE across statements, the other
wrote a column the table did not have. Both were syntactically valid, so this
suite is only a first line of defence — it still catches typos, unbalanced
parentheses and malformed CTE/RETURNING shapes locally, where a failure costs
seconds instead of a broken deploy.

``sqlglot`` is a pure-Python parser, so the check needs no database. When the
package is absent the suite skips rather than fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

sqlglot = pytest.importorskip("sqlglot", reason="sqlglot not installed")

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"

# Migrations containing PostgreSQL forms that sqlglot's dialect cannot fully
# model (PL/pgSQL DO blocks, CREATE EXTENSION, and array-typed COMMENT ON
# FUNCTION signatures) are exempt from parsing, not from review. Their
# migration-specific contract tests plus deployment readback remain mandatory.
PARSER_BLIND_SPOTS = frozenset(
    {
        "005_rls_security_policies.sql",
        "007_security_and_integrity_hardening.sql",
        "032_tenant_user_account_ownership.sql",
        "040_report_job_session_uniqueness.sql",
        "058_corporate_actions_multisource_sync.sql",
    }
)


def _migrations() -> list[Path]:
    return sorted(p for p in MIGRATIONS.glob("*.sql"))


def test_migration_directory_is_discoverable() -> None:
    paths = _migrations()
    assert paths, "no migrations found"
    assert all(path.name[:3].isdigit() for path in paths)


@pytest.mark.parametrize(
    "path",
    [p for p in _migrations() if p.name not in PARSER_BLIND_SPOTS],
    ids=lambda path: path.name,
)
def test_migration_parses_as_postgres(path: Path) -> None:
    statements = sqlglot.parse(path.read_text(encoding="utf-8"), read="postgres")
    assert any(statement is not None for statement in statements)


def test_parser_blind_spots_are_documented_and_present() -> None:
    """The exempt list must only name files that really exist."""
    known = {path.name for path in _migrations()}
    assert PARSER_BLIND_SPOTS <= known


def test_new_data_migrations_split_registration_from_seed() -> None:
    """054+: 注册与回填必须分属不同语句（CTE 并发不可见，见 054 注释）。"""
    seed = (MIGRATIONS / "054_backfill_index_fund_holdings.sql").read_text(
        encoding="utf-8"
    )
    statements = [
        statement
        for statement in sqlglot.parse(seed, read="postgres")
        if statement is not None
    ]
    inserts = [statement.sql(dialect="postgres").upper() for statement in statements]
    register_indexes = [
        index
        for index, sql in enumerate(inserts)
        if "INSERT INTO REFERENCE.INSTRUMENTS" in sql
    ]
    seed_indexes = [
        index
        for index, sql in enumerate(inserts)
        if "INSERT INTO MARKET.FUND_LOOKTHROUGH" in sql
    ]
    assert register_indexes and seed_indexes
    assert min(register_indexes) < min(seed_indexes)
    # 同一语句内既注册又回填 → 回填读不到新注册的行。
    assert not set(register_indexes) & set(seed_indexes)
