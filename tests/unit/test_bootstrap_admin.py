"""First administrator bootstrap keeps identities atomic and repeatable."""

from __future__ import annotations

import importlib
import json
from uuid import UUID

import pytest

from backend.common.auth import verify_password
from scripts.standard_report_jobs import STANDARD_REPORT_JOBS


class _Transaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.in_transaction = True
        self.conn.transaction_entries += 1
        return self.conn

    async def __aexit__(self, exc_type, *_args):
        self.conn.rolled_back = exc_type is not None
        self.conn.committed_writes = [] if exc_type else self.conn.pending_writes[:]
        self.conn.in_transaction = False
        return False


class FakeConn:
    def __init__(self, *, counts=None, existing=None, jobs=None, fail_on=None):
        self.counts = counts or {
            "profile_count": 0, "tenant_count": 0, "platform_admin_count": 0,
        }
        self.existing = existing
        self.jobs = list(_jobs() if jobs is None else jobs)
        self.fail_on = fail_on
        self.calls = []
        self.pending_writes = []
        self.committed_writes = []
        self.in_transaction = False
        self.transaction_entries = 0
        self.rolled_back = False

    def transaction(self):
        return _Transaction(self)

    def _record(self, sql, args):
        assert self.in_transaction, "all database operations require the transaction"
        self.calls.append((sql, args))

    async def execute(self, sql, *args):
        self._record(sql, args)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("simulated database failure")
        if not sql.lstrip().upper().startswith("SELECT"):
            self.pending_writes.append((sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql, *args):
        self._record(sql, args)
        if "AS profile_count" in sql:
            return self.counts
        if "AS user_id" in sql:
            return self.existing
        raise AssertionError(f"unexpected query: {sql}")

    async def fetch(self, sql, *args):
        self._record(sql, args)
        assert "workflow.jobs" in sql
        return self.jobs


def _jobs():
    return [
        {"job_name": job.name, "cron_expr": job.cron,
         "report_session": job.session, "is_enabled": True}
        for job in STANDARD_REPORT_JOBS
    ]


def _existing(**overrides):
    user_id = UUID("506e2961-303a-4687-8dc6-03f5982d83ad")
    tenant_id = UUID("2ac522ad-ecca-4eeb-b63e-9bb80155062d")
    state = {
        "user_id": user_id, "email": "admin@example.test",
        "password_hash": "$2b$existing-hash", "tenant_id": tenant_id,
        "tenant_name": "我的空间", "tenant_status": "ACTIVE",
        "membership_role": "OWNER", "membership_status": "ACTIVE",
        "default_tenant_id": tenant_id, "default_asset_scope": "MINE",
        "platform_admin_status": "ACTIVE",
    }
    state.update(overrides)
    return state


async def _bootstrap(conn, **overrides):
    module = importlib.import_module("scripts.bootstrap_admin")
    inputs = {
        "email": "admin@example.test", "password": "InitialPassword!2026",
        "nickname": "管理员", "tenant_name": "我的空间",
    }
    inputs.update(overrides)
    return await module.bootstrap_admin(conn, **inputs)


@pytest.mark.asyncio
async def test_empty_database_gets_one_admin_and_four_jobs_without_assets():
    conn = FakeConn()
    result = await _bootstrap(
        conn, email="  Admin@Example.Test ", nickname=" 管理员 ",
        tenant_name=" 我的空间 ",
    )

    assert result["created"] is True
    assert result["email"] == "admin@example.test"
    assert UUID(str(result["userId"])) != UUID(str(result["tenantId"]))
    assert conn.transaction_entries == 1
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    tables = [
        "identity.profiles", "identity.tenants", "identity.tenant_members",
        "identity.user_preferences", "identity.platform_roles", "workflow.jobs",
        "audit.audit_log",
    ]
    assert len(conn.committed_writes) == len(tables)
    for table in tables:
        assert sum(f"INSERT INTO {table}" in sql for sql, _ in conn.calls) == 1
    sql = "\n".join(statement for statement, _ in conn.calls)
    assert "identity.accounts" not in sql
    assert "ledger." not in sql
    profile_args = next(args for sql, args in conn.calls if "INSERT INTO identity.profiles" in sql)
    password_hash = next(arg for arg in profile_args if isinstance(arg, str) and arg.startswith("$2"))
    assert verify_password("InitialPassword!2026", password_hash)
    assert "InitialPassword!2026" not in repr(conn.calls)
    jobs_args = next(args for sql, args in conn.calls if "INSERT INTO workflow.jobs" in sql)
    assert jobs_args[3] == [job.session for job in STANDARD_REPORT_JOBS]
    audit_args = next(args for sql, args in conn.calls if "INSERT INTO audit.audit_log" in sql)
    details = json.loads(audit_args[-1])
    assert details["reportSchedulesInitialized"] is True
    assert "password" not in str(details).lower()


@pytest.mark.asyncio
async def test_complete_same_admin_replay_never_changes_password_or_data():
    state = _existing()
    conn = FakeConn(
        counts={"profile_count": 3, "tenant_count": 2, "platform_admin_count": 1},
        existing=state,
    )

    result = await _bootstrap(conn, password="DifferentPassword!2026", nickname="新昵称")

    assert result == {
        "created": False, "userId": str(state["user_id"]),
        "tenantId": str(state["tenant_id"]), "email": "admin@example.test",
    }
    assert conn.committed_writes == []
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in conn.calls)
    assert "password_hash" in "\n".join(sql for sql, _ in conn.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("counts", [
    {"profile_count": 1, "tenant_count": 0, "platform_admin_count": 0},
    {"profile_count": 0, "tenant_count": 1, "platform_admin_count": 0},
    {"profile_count": 1, "tenant_count": 1, "platform_admin_count": 1},
    {"profile_count": 2, "tenant_count": 1, "platform_admin_count": 2},
])
async def test_unknown_existing_identity_or_other_admin_is_rejected(counts):
    conn = FakeConn(counts=counts)
    with pytest.raises(RuntimeError, match="existing|incomplete|administrator"):
        await _bootstrap(conn)
    assert conn.committed_writes == []
    assert conn.rolled_back


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_count", [0, 2])
async def test_matching_user_cannot_be_promoted_or_hide_another_administrator(admin_count):
    conn = FakeConn(
        counts={"profile_count": 2, "tenant_count": 1, "platform_admin_count": admin_count},
        existing=_existing(),
    )
    with pytest.raises(RuntimeError, match="existing|administrator"):
        await _bootstrap(conn)
    assert conn.committed_writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"password_hash": None}, {"tenant_id": None}, {"tenant_name": "别人的空间"},
    {"tenant_status": "SUSPENDED"}, {"membership_role": "ADMIN"},
    {"membership_status": "SUSPENDED"}, {"default_tenant_id": None},
    {"default_asset_scope": "TENANT"}, {"platform_admin_status": "SUSPENDED"},
])
async def test_existing_admin_with_incomplete_relationships_is_rejected(overrides):
    conn = FakeConn(
        counts={"profile_count": 1, "tenant_count": 1, "platform_admin_count": 1},
        existing=_existing(**overrides),
    )
    with pytest.raises(RuntimeError, match="incomplete|conflict"):
        await _bootstrap(conn)
    assert conn.committed_writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("jobs", [
    [], _jobs()[:3], _jobs() + [_jobs()[0]],
    [{**job, "is_enabled": False} for job in _jobs()],
    [{**job, "cron_expr": "0 0 * * *"} for job in _jobs()],
])
async def test_existing_admin_with_missing_or_changed_jobs_is_rejected(jobs):
    conn = FakeConn(
        counts={"profile_count": 1, "tenant_count": 1, "platform_admin_count": 1},
        existing=_existing(), jobs=jobs,
    )
    with pytest.raises(RuntimeError, match="incomplete|conflict"):
        await _bootstrap(conn)
    assert conn.committed_writes == []


@pytest.mark.asyncio
async def test_failure_after_identity_writes_rolls_back_everything():
    conn = FakeConn(fail_on="INSERT INTO audit.audit_log")
    with pytest.raises(RuntimeError, match="simulated database failure"):
        await _bootstrap(conn)
    assert conn.pending_writes
    assert conn.rolled_back
    assert conn.committed_writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"email": ""}, {"email": "invalid-email"}, {"email": "a@invalid"},
    {"email": "a b@example.test"}, {"email": "a@@example.test"},
    {"password": "short"}, {"password": "a" * 73}, {"password": "中" * 25},
    {"nickname": " "}, {"nickname": "名" * 33},
    {"tenant_name": "一"}, {"tenant_name": "名" * 65},
])
async def test_invalid_input_is_rejected_before_database_access(overrides):
    conn = FakeConn()
    with pytest.raises(ValueError):
        await _bootstrap(conn, **overrides)
    assert conn.calls == []
    assert conn.transaction_entries == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["a" * 12, "a" * 72, "中" * 24])
async def test_password_accepts_character_and_utf8_byte_boundaries(password):
    conn = FakeConn()
    result = await _bootstrap(conn, password=password)
    assert result["created"] is True
