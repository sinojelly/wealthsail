"""The migration runner owns transactions, including the migration ledger write.

Historical 042--093 wrappers are immutable deployed files. Their exact outer
BEGIN/COMMIT pair is grandfathered; no later migration may control transactions.
"""

from pathlib import Path
import re

import pytest

from scripts.migrate import load_migrations


ROOT = Path(__file__).resolve().parents[2]
LEGACY_TRANSACTION_WRAPPERS = frozenset(range(42, 55)) | frozenset(range(56, 94))
DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def _top_level_statements(sql: str) -> list[str]:
    """Split PostgreSQL statements, excluding comments and quoted bodies.

    PL/pgSQL BEGIN/END belongs to a quoted function/DO body, and therefore must
    not be confused with transaction control issued by the migration itself.
    """
    statements: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(sql):
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            index = len(sql) if end < 0 else end + 1
            current.append(" ")
        elif sql.startswith("/*", index):
            depth = 1
            index += 2
            while depth and index < len(sql):
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            assert depth == 0, "unterminated migration SQL comment"
            current.append(" ")
        elif sql[index] in ("'", '"'):
            quote = sql[index]
            index += 1
            while index < len(sql):
                if sql[index] == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                # PostgreSQL escape string constants use backslash escapes.
                if sql[index] == "\\":
                    index += 2
                else:
                    index += 1
            current.append(" quoted ")
        elif match := DOLLAR_QUOTE.match(sql, index):
            end = sql.find(match.group(), match.end())
            assert end >= 0, "unterminated migration SQL dollar quote"
            index = end + len(match.group())
            current.append(" quoted ")
        elif sql[index] == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current.clear()
            index += 1
        else:
            current.append(sql[index])
            index += 1
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def _transaction_statements(sql: str) -> list[str]:
    commands: list[str] = []
    for statement in _top_level_statements(sql):
        words = re.findall(r"[A-Za-z_]+", statement.upper())
        if words and (
            words[0]
            in {"BEGIN", "COMMIT", "ROLLBACK", "END", "ABORT", "SAVEPOINT", "RELEASE"}
            or words[:2]
            in (
                ["START", "TRANSACTION"],
                ["PREPARE", "TRANSACTION"],
                ["SET", "TRANSACTION"],
            )
        ):
            commands.append(" ".join(words))
    return commands


def test_every_migration_keeps_transaction_ownership_with_the_runner():
    legacy_wrappers_found: set[int] = set()
    violations: list[str] = []
    for migration in load_migrations(ROOT / "migrations"):
        controls = _transaction_statements(migration.sql)
        if migration.version in LEGACY_TRANSACTION_WRAPPERS:
            # Never silently enlarge this exception set or mutate deployed SQL.
            assert controls == ["BEGIN", "COMMIT"], migration.path.name
            statements = _top_level_statements(migration.sql)
            assert statements[0].upper() == "BEGIN", migration.path.name
            assert statements[-1].upper() == "COMMIT", migration.path.name
            legacy_wrappers_found.add(migration.version)
        elif controls:
            violations.append(f"{migration.path.name}: {controls}")
    assert legacy_wrappers_found == LEGACY_TRANSACTION_WRAPPERS
    assert not violations, (
        "SQL must not end the runner/ledger transaction: " + "; ".join(violations)
    )


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("/* BEGIN; /* ROLLBACK; */ COMMIT; */ SELECT 1; -- COMMIT;", []),
        ("DO $body$ BEGIN RAISE NOTICE 'COMMIT;'; END $body$;", []),
        (
            "CREATE FUNCTION f() RETURNS void AS $$ BEGIN RETURN; END $$ LANGUAGE plpgsql;",
            [],
        ),
        ("SELECT 'don''t COMMIT;', \"ROLLBACK;\";", []),
        ("begin; SELECT 1; CoMmIt;", ["BEGIN", "COMMIT"]),
        ("-- safe-looking comment\nROLLBACK;", ["ROLLBACK"]),
        ("START TRANSACTION; END;", ["START TRANSACTION", "END"]),
        ("PREPARE TRANSACTION 'x';", ["PREPARE TRANSACTION QUOTED"]),
    ],
)
def test_transaction_detection_ignores_function_bodies_and_literals(sql, expected):
    assert _transaction_statements(sql) == expected
