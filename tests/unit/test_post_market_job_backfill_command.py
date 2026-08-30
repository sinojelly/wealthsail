"""Behavior tests for the controlled POST_MARKET tenant-job backfill."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from uuid import UUID

import pytest

from scripts import backfill_post_market_job as command


TENANT_ID = UUID("8c1b5af8-772d-4abe-83cc-1eea1519a21a")
JOB_ID = UUID("70000000-0000-0000-0000-000000000004")


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
    """A stateful database boundary fake; assertions target resulting state."""

    def __init__(
        self,
        *,
        tenant: dict | None = None,
        jobs: list[dict] | None = None,
    ) -> None:
        self.tenant = tenant or {
            "id": TENANT_ID,
            "name": "so",
            "status": "ACTIVE",
        }
        self.jobs = list(jobs or [])
        self.audit_rows: list[dict] = []
        self.transaction_entries = 0
        self.transaction_exits = 0
        self.rolled_back = False
        self.tenant_lock_used = False

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, sql: str, *args):
        if "FROM identity.tenants" in sql:
            self.tenant_lock_used = "FOR UPDATE" in sql
            return self.tenant if self.tenant["id"] == args[0] else None
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql: str, *args):
        if "FROM workflow.jobs" in sql:
            return list(self.jobs)
        raise AssertionError(f"unexpected fetch: {sql}")

    async def fetchval(self, sql: str, *args):
        if "INSERT INTO workflow.jobs" not in sql:
            raise AssertionError(f"unexpected fetchval: {sql}")
        row = {
            "id": JOB_ID,
            "job_name": args[1],
            "cron_expr": args[2],
            "report_session": "POST_MARKET",
            "is_enabled": True,
        }
        self.jobs.append(row)
        return JOB_ID

    async def execute(self, sql: str, *args):
        if "INSERT INTO audit.audit_log" not in sql:
            raise AssertionError(f"unexpected execute: {sql}")
        self.audit_rows.append(
            {
                "tenant_id": args[0],
                "job_id": args[1],
                "details": json.loads(args[2]),
            }
        )
        return "INSERT 0 1"


def _request(**overrides):
    values = {"tenant_id": TENANT_ID, "tenant_name": "so"}
    values.update(overrides)
    return command.BackfillRequest(**values)


def _canonical_job(**overrides) -> dict:
    row = {
        "id": JOB_ID,
        "job_name": "post_market_review",
        "cron_expr": "0 30 15 * * 1-5",
        "report_session": "POST_MARKET",
        "is_enabled": True,
    }
    row.update(overrides)
    return row


def test_backfill_command_can_be_launched_by_documented_file_path() -> None:
    """Operators must not need a custom PYTHONPATH to reach the safety CLI."""

    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "backfill_post_market_job.py"),
            "--help",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--tenant-id" in result.stdout
    assert "--tenant-name" in result.stdout
    assert "--apply" in result.stdout


@pytest.mark.asyncio
async def test_preflight_is_read_only_and_locks_the_exact_active_tenant() -> None:
    conn = FakeConn()

    result = await command.backfill_post_market_job(conn, _request(), apply=False)

    assert result == {
        "status": "READY",
        "tenantId": str(TENANT_ID),
        "tenantName": "so",
        "reportSession": "POST_MARKET",
        "jobName": "post_market_review",
        "cronExpr": "0 30 15 * * 1-5",
    }
    assert conn.jobs == []
    assert conn.audit_rows == []
    assert conn.tenant_lock_used is True
    assert conn.transaction_entries == conn.transaction_exits == 1


@pytest.mark.asyncio
async def test_apply_inserts_one_canonical_job_and_an_audit_record() -> None:
    conn = FakeConn()

    result = await command.backfill_post_market_job(conn, _request(), apply=True)

    assert result["status"] == "APPLIED"
    assert result["jobId"] == str(JOB_ID)
    assert conn.jobs == [_canonical_job()]
    assert conn.audit_rows[0]["tenant_id"] == TENANT_ID
    # The SQL parameter is explicitly cast to TEXT; passing UUID would make
    # asyncpg reject the query before PostgreSQL can write the audit row.
    assert conn.audit_rows[0]["job_id"] == str(JOB_ID)
    assert conn.audit_rows[0]["details"]["operation"] == "MANUAL_BACKFILL"
    assert conn.rolled_back is False


@pytest.mark.asyncio
async def test_exact_existing_job_is_idempotent_without_a_second_audit() -> None:
    conn = FakeConn(jobs=[_canonical_job()])

    result = await command.backfill_post_market_job(conn, _request(), apply=True)

    assert result["status"] == "ALREADY_APPLIED"
    assert result["jobId"] == str(JOB_ID)
    assert conn.jobs == [_canonical_job()]
    assert conn.audit_rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "jobs",
    [
        [_canonical_job(is_enabled=False)],
        [_canonical_job(cron_expr="0 0 16 * * 1-5")],
        [_canonical_job(), _canonical_job(id=UUID(int=5))],
    ],
)
async def test_conflicting_or_duplicate_jobs_abort_without_writes(jobs) -> None:
    conn = FakeConn(jobs=jobs)

    with pytest.raises(RuntimeError, match="existing POST_MARKET job state is unsafe"):
        await command.backfill_post_market_job(conn, _request(), apply=True)

    assert conn.jobs == jobs
    assert conn.audit_rows == []
    assert conn.rolled_back is True


@pytest.mark.asyncio
async def test_tenant_name_mismatch_aborts_before_job_access() -> None:
    conn = FakeConn()

    with pytest.raises(RuntimeError, match="tenant identity did not match"):
        await command.backfill_post_market_job(
            conn,
            _request(tenant_name="not-so"),
            apply=True,
        )

    assert conn.jobs == []
    assert conn.audit_rows == []
    assert conn.rolled_back is True


def test_apply_confirmations_must_match_target_and_tenant_exactly() -> None:
    request = _request()

    command.validate_apply_confirmations(
        request,
        target="db.example:5432/postgres",
        confirm_target="db.example:5432/postgres",
        confirm_tenant_id=str(TENANT_ID),
        confirm_tenant_name="so",
    )

    with pytest.raises(SystemExit, match="tenant name confirmation mismatch"):
        command.validate_apply_confirmations(
            request,
            target="db.example:5432/postgres",
            confirm_target="db.example:5432/postgres",
            confirm_tenant_id=str(TENANT_ID),
            confirm_tenant_name="SO",
        )


class RunConn:
    def __init__(self, *, current_user: str, migration_ready: bool) -> None:
        self.current_user = current_user
        self.migration_ready = migration_ready
        self.closed = False

    async def fetchval(self, sql: str, *_args):
        if "current_user" in sql:
            return self.current_user
        if "wealthsail_schema_migrations" in sql:
            return self.migration_ready
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def close(self) -> None:
        self.closed = True


def _run_args() -> Namespace:
    return Namespace(
        tenant_id=TENANT_ID,
        tenant_name="so",
        apply=False,
        confirm_target=None,
        confirm_tenant_id=None,
        confirm_tenant_name=None,
    )


@pytest.mark.asyncio
async def test_runtime_database_role_is_rejected(monkeypatch) -> None:
    conn = RunConn(current_user="wealthsail_worker", migration_ready=True)

    async def connect(_dsn):
        return conn

    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql://worker:secret@db.example:5432/postgres",
    )
    monkeypatch.setattr(command.asyncpg, "connect", connect)

    with pytest.raises(SystemExit, match="migration/admin database role"):
        await command._run(_run_args())

    assert conn.closed is True


@pytest.mark.asyncio
async def test_migration_033_is_required_before_backfill(monkeypatch) -> None:
    conn = RunConn(current_user="postgres", migration_ready=False)

    async def connect(_dsn):
        return conn

    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql://admin:secret@db.example:5432/postgres",
    )
    monkeypatch.setattr(command.asyncpg, "connect", connect)

    with pytest.raises(SystemExit, match="migration 033 must be applied"):
        await command._run(_run_args())

    assert conn.closed is True
