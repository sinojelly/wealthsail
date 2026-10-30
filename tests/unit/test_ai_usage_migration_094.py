
"""Static contract tests for forward-only AI usage migration 094."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"

M093 = MIGRATIONS / "093_fix_ai_usage_overlap_json_and_role.sql"
M094 = MIGRATIONS / "094_harden_ai_usage_rls.sql"


def sql_without_comments(path: Path) -> str:
    assert path.is_file(), f"Missing migration: {path.name}"
    sql = path.read_text(encoding="utf-8")
    return re.sub(r"--[^\n]*", "", sql)


def test_093_preserves_production_checksum():
    """Production has already applied the 0047741 version of 093."""
    sql = M093.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

    assert checksum.startswith("4b88cc2bcabb"), (
        f"093 checksum drift: {checksum}"
    )


def test_094_only_replaces_two_expected_policies():
    """094 must be a narrow, forward-only RLS change."""
    sql = sql_without_comments(M094)

    assert sql.strip().startswith("BEGIN;")
    assert sql.strip().endswith("COMMIT;")

    created = re.findall(
        r"\bCREATE\s+POLICY\s+(\w+)", sql, re.IGNORECASE
    )
    dropped = re.findall(
        r"\bDROP\s+POLICY\s+IF\s+EXISTS\s+(\w+)",
        sql,
        re.IGNORECASE,
    )

    expected = ["ai_usage_task_insert", "ai_usage_task_read"]
    assert sorted(created) == expected
    assert sorted(dropped) == expected

    assert len(re.findall(
        r"\bON\s+research\.ai_usage_tasks\b",
        sql,
        re.IGNORECASE,
    )) == 4

    forbidden = [
        r"\bCREATE\s+TABLE\b",
        r"\bALTER\s+TABLE\b",
        r"\bDROP\s+TABLE\b",
        r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b",
        r"\bINSERT\s+INTO\b",
        r"\bDELETE\s+FROM\b",
        r"\bUPDATE\s+\w+",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, sql, re.IGNORECASE), pattern


def test_094_read_policy_uses_safe_identity():
    sql = sql_without_comments(M094)

    read_policy = sql.split(
        "CREATE POLICY ai_usage_task_read", 1
    )[1].split(
        "DROP POLICY IF EXISTS ai_usage_task_insert", 1
    )[0]

    assert "FOR SELECT TO wealthsail_api" in read_policy
    assert "research.safe_jwt_claim('tenant_id')" in read_policy
    assert "user_id = research.safe_jwt_uid()" in read_policy

    assert "identity.tenant_members" in read_policy
    assert "tm.status = 'ACTIVE'" in read_policy
    assert "tm.role = ANY (ARRAY['OWNER', 'ADMIN'])" in read_policy

    assert "identity.platform_roles" in read_policy
    assert "PLATFORM_ADMIN" in read_policy


def test_094_insert_policy_rejects_invalid_identity():
    sql = sql_without_comments(M094)

    insert_policy = sql.split(
        "CREATE POLICY ai_usage_task_insert", 1
    )[1].split("COMMIT;", 1)[0]

    assert "FOR INSERT TO wealthsail_api" in insert_policy
    assert "WITH CHECK" in insert_policy
    assert "research.safe_jwt_claim('tenant_id')" in insert_policy
    assert "user_id = research.safe_jwt_uid()" in insert_policy
    assert "research.safe_jwt_uid() IS NOT NULL" in insert_policy

    assert "identity.tenant_members" in insert_policy
    assert "tm.status = 'ACTIVE'" in insert_policy


def test_094_has_no_unsafe_legacy_identity_dependency():
    sql = sql_without_comments(M094)

    assert "auth.uid()" not in sql
    assert "identity.has_tenant_role" not in sql
    assert "identity.is_tenant_member" not in sql

    assert "wealthsail_worker" not in sql
    assert "SECURITY DEFINER" not in sql
