"""Safety behavior for the controlled existing-user personal-tenant migration."""

from __future__ import annotations

from uuid import UUID

import pytest

from scripts.migrate_user_to_personal_tenant import (
    MigrationRequest,
    migrate_user,
    personal_tenant_id,
)


class _Transaction:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn

    async def __aenter__(self):
        self.conn.transaction_entries += 1
        return self.conn

    async def __aexit__(self, exc_type, *_args):
        self.conn.transaction_exits += 1
        self.conn.rolled_back = exc_type is not None
        return False


class FakeConn:
    def __init__(self, *, state: dict, target: dict | None = None) -> None:
        self.state = state
        self.target = target
        self.calls: list[tuple[str, tuple]] = []
        self.transaction_entries = 0
        self.transaction_exits = 0
        self.rolled_back = False
        self.fact_reads = 0

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, sql: str, *args):
        self.calls.append((sql, args))
        if "AS source_membership_status" in sql:
            return self.state
        if "AS target_tenant_name" in sql:
            return self.target
        if "AS account_count" in sql:
            self.fact_reads += 1
            return {
                "account_count": 1,
                "ledger_event_count": 1,
                "position_snapshot_count": 1,
                "nav_snapshot_count": 1,
                "import_batch_count": 1,
            }
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql: str, *args):
        self.calls.append((sql, args))
        return "UPDATE 1"


def _request(**overrides) -> MigrationRequest:
    data = {
        "email": "jelly@example.test",
        "source_tenant_id": UUID("8c1b5af8-772d-4abe-83cc-1eea1519a21a"),
        "personal_tenant_name": "Jelly 的个人空间",
        "grant_platform_admin": True,
    }
    data.update(overrides)
    return MigrationRequest(**data)


def _state(**overrides) -> dict:
    data = {
        "user_id": UUID("d44c3257-7e2e-4f2d-84fa-ed2c07a44b57"),
        "email": "jelly@example.test",
        "nickname": "Jelly",
        "source_tenant_name": "so",
        "source_role": "ADMIN",
        "source_membership_status": "ACTIVE",
        "remaining_active_owner_count": 1,
        "explicit_owned_account_count": 0,
        "default_tenant_id": UUID("8c1b5af8-772d-4abe-83cc-1eea1519a21a"),
        "platform_admin_status": None,
    }
    data.update(overrides)
    return data


def test_personal_tenant_id_is_stable_per_user_and_not_the_source_tenant() -> None:
    user_id = UUID("d44c3257-7e2e-4f2d-84fa-ed2c07a44b57")

    first = personal_tenant_id(user_id)
    second = personal_tenant_id(user_id)

    assert first == second
    assert first != UUID("8c1b5af8-772d-4abe-83cc-1eea1519a21a")


@pytest.mark.asyncio
async def test_dry_run_performs_read_only_preflight() -> None:
    conn = FakeConn(state=_state())

    result = await migrate_user(conn, _request(), apply=False)

    assert result["status"] == "READY"
    assert result["sourceRole"] == "ADMIN"
    assert result["explicitOwnedAccountCount"] == 0
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _args in conn.calls)
    assert conn.transaction_entries == 1
    assert conn.transaction_exits == 1


@pytest.mark.asyncio
async def test_apply_changes_only_identity_session_and_audit_state() -> None:
    conn = FakeConn(state=_state())

    result = await migrate_user(conn, _request(), apply=True)

    sql = "\n".join(statement for statement, _args in conn.calls)
    assert result["status"] == "APPLIED"
    assert "INSERT INTO identity.tenants" in sql
    assert "INSERT INTO identity.tenant_members" in sql
    assert "INSERT INTO identity.user_preferences" in sql
    assert "INSERT INTO identity.platform_roles" in sql
    assert "UPDATE identity.tenant_members" in sql
    assert "status = 'SUSPENDED'" in sql
    assert "auth_version = auth_version + 1" in sql
    assert "UPDATE identity.refresh_sessions" in sql
    assert sql.count("INSERT INTO audit.audit_log") == 2
    for forbidden in (
        "UPDATE ledger.",
        "DELETE FROM ledger.",
        "INSERT INTO ledger.",
        "UPDATE identity.accounts",
        "DELETE FROM identity.accounts",
    ):
        assert forbidden not in sql
    assert conn.fact_reads == 2
    assert not conn.rolled_back


@pytest.mark.asyncio
async def test_replay_is_read_only_when_the_exact_result_already_exists() -> None:
    target_id = personal_tenant_id(_state()["user_id"])
    conn = FakeConn(
        state=_state(
            source_membership_status="SUSPENDED",
            default_tenant_id=target_id,
            platform_admin_status="ACTIVE",
        ),
        target={
            "target_tenant_name": "Jelly 的个人空间",
            "target_membership_role": "OWNER",
            "target_membership_status": "ACTIVE",
        },
    )

    result = await migrate_user(conn, _request(), apply=True)

    assert result["status"] == "ALREADY_APPLIED"
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _args in conn.calls)


@pytest.mark.asyncio
async def test_explicit_owned_account_aborts_before_any_write() -> None:
    conn = FakeConn(state=_state(explicit_owned_account_count=1))

    with pytest.raises(RuntimeError, match="explicitly owned account"):
        await migrate_user(conn, _request(), apply=True)

    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _args in conn.calls)
    assert conn.rolled_back
